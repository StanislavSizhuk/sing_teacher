package auth

import (
	"context"
	"errors"
	"fmt"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/security"
)

// stateTokenBytes/verifierTokenBytes are sized so the resulting base64url
// PKCE code_verifier lands well inside RFC 7636's 43-128 character range.
const (
	stateTokenBytes    = 32
	verifierTokenBytes = 64
)

// GoogleAuthStart begins the OAuth2/PKCE flow: it mints state and a PKCE code
// verifier, and returns the URL to send the browser to. The caller
// (transport) is responsible for stashing state and codeVerifier in
// short-lived httpOnly cookies until the callback arrives.
func (s *Service) GoogleAuthStart(ctx context.Context) (authURL, state, codeVerifier string, err error) {
	state, err = security.RandomURLSafeToken(stateTokenBytes)
	if err != nil {
		return "", "", "", err
	}
	codeVerifier, err = security.RandomURLSafeToken(verifierTokenBytes)
	if err != nil {
		return "", "", "", err
	}
	authURL, err = s.google.AuthCodeURL(ctx, state, codeVerifier)
	if err != nil {
		return "", "", "", err
	}
	return authURL, state, codeVerifier, nil
}

// GoogleCallback completes the flow: verifies the CSRF state, exchanges the
// code for a verified identity, and either logs in the linked account, links
// Google to a matching verified-email account, or creates a new account.
func (s *Service) GoogleCallback(ctx context.Context, code, codeVerifier, gotState, expectedState string) (*Session, error) {
	if expectedState == "" || gotState != expectedState {
		return nil, domain.ErrOAuthState
	}

	identity, err := s.google.Exchange(ctx, code, codeVerifier)
	if err != nil {
		return nil, fmt.Errorf("exchange google identity: %w", err)
	}

	if user, err := s.users.GetByGoogleID(ctx, identity.Subject); err == nil {
		return s.issueSession(ctx, user.ID)
	} else if !errors.Is(err, domain.ErrNotFound) {
		return nil, fmt.Errorf("look up user by google id: %w", err)
	}

	// Only ever link/create using an email Google itself has verified --
	// otherwise a Google account with an unverified address could be used to
	// take over an existing account of the same address (spec 9.1).
	if !identity.EmailVerified {
		return nil, domain.ErrGoogleEmailNotVerified
	}
	email := normalizeEmail(identity.Email)

	if existing, err := s.users.GetByEmail(ctx, email); err == nil {
		if err := s.users.LinkGoogleID(ctx, existing.ID, identity.Subject); err != nil {
			return nil, fmt.Errorf("link google identity: %w", err)
		}
		return s.issueSession(ctx, existing.ID)
	} else if !errors.Is(err, domain.ErrNotFound) {
		return nil, fmt.Errorf("look up user by email: %w", err)
	}

	displayName := identity.Name
	if displayName == "" {
		displayName = email
	}
	googleID := identity.Subject
	newUser := &domain.User{
		ID:            uuid.New(),
		Email:         email,
		GoogleID:      &googleID,
		DisplayName:   displayName,
		EmailVerified: true,
	}
	if err := s.users.Create(ctx, newUser); err != nil {
		return nil, fmt.Errorf("create user from google identity: %w", err)
	}
	return s.issueSession(ctx, newUser.ID)
}
