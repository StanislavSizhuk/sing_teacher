// Package oauth implements the auth service's GoogleVerifier over Google's
// OpenID Connect provider, with PKCE (spec 9.1).
package oauth

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"fmt"
	"sync"

	"github.com/coreos/go-oidc/v3/oidc"
	"golang.org/x/oauth2"
	"golang.org/x/oauth2/google"

	"ai-vocal-coach/api/internal/service/auth"
)

// GoogleVerifier drives the Google OAuth2 + OIDC flow. It initializes lazily
// on first use rather than at boot, so a transient outage reaching Google's
// discovery endpoint does not take down email/password login or health checks.
type GoogleVerifier struct {
	clientID, clientSecret, redirectURL string

	mu       sync.Mutex
	config   *oauth2.Config
	verifier *oidc.IDTokenVerifier
}

// NewGoogleVerifier builds a verifier for the given OAuth client.
func NewGoogleVerifier(clientID, clientSecret, redirectURL string) *GoogleVerifier {
	return &GoogleVerifier{clientID: clientID, clientSecret: clientSecret, redirectURL: redirectURL}
}

func (g *GoogleVerifier) ensureInit(ctx context.Context) error {
	g.mu.Lock()
	defer g.mu.Unlock()
	if g.config != nil {
		return nil
	}
	provider, err := oidc.NewProvider(ctx, "https://accounts.google.com")
	if err != nil {
		return fmt.Errorf("discover google oidc provider: %w", err)
	}
	g.verifier = provider.Verifier(&oidc.Config{ClientID: g.clientID})
	g.config = &oauth2.Config{
		ClientID:     g.clientID,
		ClientSecret: g.clientSecret,
		RedirectURL:  g.redirectURL,
		Endpoint:     google.Endpoint,
		Scopes:       []string{oidc.ScopeOpenID, "email", "profile"},
	}
	return nil
}

func codeChallengeS256(codeVerifier string) string {
	sum := sha256.Sum256([]byte(codeVerifier))
	return base64.RawURLEncoding.EncodeToString(sum[:])
}

// AuthCodeURL returns the Google consent-screen URL for this state and PKCE verifier.
func (g *GoogleVerifier) AuthCodeURL(ctx context.Context, state, codeVerifier string) (string, error) {
	if err := g.ensureInit(ctx); err != nil {
		return "", err
	}
	url := g.config.AuthCodeURL(state,
		oauth2.SetAuthURLParam("code_challenge", codeChallengeS256(codeVerifier)),
		oauth2.SetAuthURLParam("code_challenge_method", "S256"),
	)
	return url, nil
}

// Exchange trades an authorization code for a verified Google identity.
func (g *GoogleVerifier) Exchange(ctx context.Context, code, codeVerifier string) (auth.GoogleIdentity, error) {
	if err := g.ensureInit(ctx); err != nil {
		return auth.GoogleIdentity{}, err
	}

	token, err := g.config.Exchange(ctx, code, oauth2.SetAuthURLParam("code_verifier", codeVerifier))
	if err != nil {
		return auth.GoogleIdentity{}, fmt.Errorf("exchange google authorization code: %w", err)
	}

	rawIDToken, ok := token.Extra("id_token").(string)
	if !ok {
		return auth.GoogleIdentity{}, errors.New("google token response missing id_token")
	}
	idToken, err := g.verifier.Verify(ctx, rawIDToken)
	if err != nil {
		return auth.GoogleIdentity{}, fmt.Errorf("verify google id token: %w", err)
	}

	var claims struct {
		Subject       string `json:"sub"`
		Email         string `json:"email"`
		EmailVerified bool   `json:"email_verified"`
		Name          string `json:"name"`
	}
	if err := idToken.Claims(&claims); err != nil {
		return auth.GoogleIdentity{}, fmt.Errorf("parse google id token claims: %w", err)
	}

	return auth.GoogleIdentity{
		Subject:       claims.Subject,
		Email:         claims.Email,
		EmailVerified: claims.EmailVerified,
		Name:          claims.Name,
	}, nil
}
