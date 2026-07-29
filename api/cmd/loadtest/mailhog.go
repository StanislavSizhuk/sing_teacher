package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"time"
)

// verificationCodePattern matches the exact wording
// internal/mailer.SMTPMailer.SendVerificationCode sends: "Your verification
// code is 123456. It expires in 24 hours."
var verificationCodePattern = regexp.MustCompile(`code is (\d{6})`)

type mailhogMessage struct {
	Content struct {
		Body string `json:"Body"`
	} `json:"Content"`
}

type mailhogSearchResult struct {
	Items []mailhogMessage `json:"items"`
}

// codeFetchAttempts and codeFetchInterval bound how long fetchVerificationCode
// polls mailhog for an email that SMTP delivery hasn't necessarily landed by
// the time register's HTTP response comes back.
const (
	codeFetchAttempts = 20
	codeFetchInterval = 300 * time.Millisecond
)

// fetchVerificationCode polls mailhog's search API (deploy/docker-compose.dev.yml)
// for the most recent verification email sent to `to` and extracts its
// 6-digit code.
func fetchVerificationCode(ctx context.Context, mailhogBase, to string) (string, error) {
	client := &http.Client{Timeout: 5 * time.Second}
	searchURL := mailhogBase + "/api/v2/search?kind=to&query=" + url.QueryEscape(to)

	for attempt := 0; attempt < codeFetchAttempts; attempt++ {
		if code, ok := tryFetchCode(ctx, client, searchURL); ok {
			return code, nil
		}
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-time.After(codeFetchInterval):
		}
	}
	return "", fmt.Errorf("no verification email reached mailhog for %s after %d attempts", to, codeFetchAttempts)
}

func tryFetchCode(ctx context.Context, client *http.Client, searchURL string) (code string, ok bool) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, searchURL, nil)
	if err != nil {
		return "", false
	}
	resp, err := client.Do(req)
	if err != nil {
		return "", false
	}
	defer func() { _ = resp.Body.Close() }()

	var result mailhogSearchResult
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil || len(result.Items) == 0 {
		return "", false
	}
	m := verificationCodePattern.FindStringSubmatch(result.Items[0].Content.Body)
	if m == nil {
		return "", false
	}
	return m[1], true
}
