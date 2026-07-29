package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"strings"
	"time"
)

// requestTimeout bounds a single HTTP call. addSong and enqueueAnalysis both
// carry a small in-memory file, so this stays generous without risking a
// hung run on a genuinely dead server.
const requestTimeout = 20 * time.Second

// problemDetails mirrors internal/transport/http.problemDetails (spec 8.1,
// RFC 9457): the one shape every non-2xx go-api response takes.
type problemDetails struct {
	Code   string `json:"code"`
	Detail string `json:"detail"`
	Status int    `json:"status"`
}

// client is a minimal, purpose-built HTTP client for the load test --
// deliberately not the generated OpenAPI client web/ uses, since this tool
// lives in api/ and only needs a handful of calls.
type client struct {
	http *http.Client
	base string
}

func newClient(base string) *client {
	return &client{http: &http.Client{Timeout: requestTimeout}, base: strings.TrimRight(base, "/")}
}

func (c *client) apiURL(path string) string  { return c.base + "/api/v1" + path }
func (c *client) rootURL(path string) string { return c.base + path }

// checkHealth requests an unauthenticated root-level endpoint (/healthz or
// /readyz, spec 8.2) and fails unless it reports 200.
func (c *client) checkHealth(ctx context.Context, path string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.rootURL(path), nil)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s returned %d", path, resp.StatusCode)
	}
	return nil
}

func (c *client) postJSON(ctx context.Context, path, token string, payload any) (*http.Response, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.apiURL(path), bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	setAuth(req, token)
	return c.http.Do(req)
}

func setAuth(req *http.Request, token string) {
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
}

// unexpectedStatus turns a non-2xx response into an error carrying the
// RFC 9457 code when the body has one, so a run failure names the exact
// server-side reason instead of just a status number.
func unexpectedStatus(resp *http.Response) error {
	var p problemDetails
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	_ = json.Unmarshal(body, &p)
	if p.Code != "" {
		return fmt.Errorf("unexpected status %d (%s): %s", resp.StatusCode, p.Code, p.Detail)
	}
	return fmt.Errorf("unexpected status %d", resp.StatusCode)
}

// register calls POST /auth/register (FR-01). The response never reveals
// whether the email already existed, so a 202 is all there is to check.
func (c *client) register(ctx context.Context, email, password, displayName string) error {
	resp, err := c.postJSON(ctx, "/auth/register", "", map[string]string{
		"email": email, "password": password, "display_name": displayName,
	})
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusAccepted {
		return unexpectedStatus(resp)
	}
	return nil
}

// verify calls POST /auth/verify (FR-03) with the code fetched from mailhog.
func (c *client) verify(ctx context.Context, email, code string) error {
	resp, err := c.postJSON(ctx, "/auth/verify", "", map[string]string{"email": email, "code": code})
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return unexpectedStatus(resp)
	}
	return nil
}

// login calls POST /auth/login and returns the access token (spec 9). The
// rotating refresh cookie is irrelevant here -- this run's tokens live only
// as long as the process does.
func (c *client) login(ctx context.Context, email, password string) (accessToken string, err error) {
	resp, err := c.postJSON(ctx, "/auth/login", "", map[string]string{"email": email, "password": password})
	if err != nil {
		return "", err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return "", unexpectedStatus(resp)
	}
	var session struct {
		AccessToken string `json:"access_token"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&session); err != nil {
		return "", fmt.Errorf("decode session response: %w", err)
	}
	return session.AccessToken, nil
}

// addSong calls POST /songs with source_type=upload (FR-10) and returns the
// new song's id.
func (c *client) addSong(ctx context.Context, token, title string, wav []byte) (songID string, err error) {
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	if err := mw.WriteField("source_type", "upload"); err != nil {
		return "", err
	}
	if err := mw.WriteField("title", title); err != nil {
		return "", err
	}
	fw, err := mw.CreateFormFile("file", "song.wav")
	if err != nil {
		return "", err
	}
	if _, err := fw.Write(wav); err != nil {
		return "", err
	}
	if err := mw.Close(); err != nil {
		return "", err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.apiURL("/songs"), &buf)
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", mw.FormDataContentType())
	setAuth(req, token)

	resp, err := c.http.Do(req)
	if err != nil {
		return "", err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusCreated {
		return "", unexpectedStatus(resp)
	}
	var song struct {
		ID string `json:"id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&song); err != nil {
		return "", fmt.Errorf("decode song response: %w", err)
	}
	return song.ID, nil
}

// enqueueOutcome is the burst's unit of measurement. A non-nil error from
// enqueueAnalysis means the request never got an HTTP response at all
// (connection refused/reset/timeout) -- the one outcome that actually
// points at the server going down under load. Every other outcome,
// including 429, is captured here as data, not a Go error, because 429
// QUEUE_FULL is an expected, correct response once the queue is at
// capacity (spec 10, FR-24).
type enqueueOutcome struct {
	StatusCode  int
	AnalysisID  string
	ProblemCode string
	Latency     time.Duration
}

// enqueueAnalysis calls POST /analyses (FR-22) with a small synthetic
// recording.
func (c *client) enqueueAnalysis(ctx context.Context, token, songID string, recording []byte) (enqueueOutcome, error) {
	var buf bytes.Buffer
	mw := multipart.NewWriter(&buf)
	if err := mw.WriteField("song_id", songID); err != nil {
		return enqueueOutcome{}, err
	}
	fw, err := mw.CreateFormFile("recording", "recording.wav")
	if err != nil {
		return enqueueOutcome{}, err
	}
	if _, err := fw.Write(recording); err != nil {
		return enqueueOutcome{}, err
	}
	if err := mw.Close(); err != nil {
		return enqueueOutcome{}, err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.apiURL("/analyses"), &buf)
	if err != nil {
		return enqueueOutcome{}, err
	}
	req.Header.Set("Content-Type", mw.FormDataContentType())
	setAuth(req, token)

	start := time.Now()
	resp, err := c.http.Do(req)
	if err != nil {
		return enqueueOutcome{}, fmt.Errorf("request failed: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()

	out := enqueueOutcome{StatusCode: resp.StatusCode, Latency: time.Since(start)}
	if resp.StatusCode == http.StatusAccepted {
		var a struct {
			ID string `json:"id"`
		}
		if err := json.NewDecoder(resp.Body).Decode(&a); err != nil {
			return out, fmt.Errorf("decode analysis response: %w", err)
		}
		out.AnalysisID = a.ID
		return out, nil
	}
	var p problemDetails
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	_ = json.Unmarshal(body, &p)
	out.ProblemCode = p.Code
	return out, nil
}

// cancelAnalysis calls POST /analyses/{id}/cancel (FR-25), used to clean up
// a run's queued jobs afterward rather than leave them for a real worker to
// churn through with synthetic audio.
func (c *client) cancelAnalysis(ctx context.Context, token, analysisID string) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.apiURL("/analyses/"+analysisID+"/cancel"), nil)
	if err != nil {
		return err
	}
	setAuth(req, token)
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return unexpectedStatus(resp)
	}
	return nil
}
