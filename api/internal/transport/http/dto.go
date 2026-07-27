package httptransport

import (
	"errors"
	"net/mail"
	"regexp"
	"strings"
	"time"

	"ai-vocal-coach/api/internal/domain"
)

// Boundary limits are deliberately generous but finite: they exist to reject
// abusive payloads before they reach business logic, not to enforce policy
// (spec 12.1 -- policy, like the 10-character password minimum, lives in the
// service layer so it stays in one place).
const (
	maxEmailLength       = 254
	maxPasswordLength    = 256
	minDisplayNameLength = 1
	maxDisplayNameLength = 100
)

var verificationCodePattern = regexp.MustCompile(`^\d{6}$`)

func validateEmail(raw string) (string, error) {
	email := strings.TrimSpace(raw)
	if email == "" || len(email) > maxEmailLength {
		return "", errors.New("email is required")
	}
	addr, err := mail.ParseAddress(email)
	if err != nil || addr.Address != email {
		return "", errors.New("email must be a valid address")
	}
	return email, nil
}

func validatePassword(raw string) (string, error) {
	if raw == "" || len(raw) > maxPasswordLength {
		return "", errors.New("password is required")
	}
	return raw, nil
}

func validateDisplayName(raw string) (string, error) {
	name := strings.TrimSpace(raw)
	if len(name) < minDisplayNameLength || len(name) > maxDisplayNameLength {
		return "", errors.New("display name must be between 1 and 100 characters")
	}
	return name, nil
}

func validateVerificationCode(raw string) (string, error) {
	code := strings.TrimSpace(raw)
	if !verificationCodePattern.MatchString(code) {
		return "", errors.New("code must be exactly 6 digits")
	}
	return code, nil
}

type registerRequest struct {
	Email       string `json:"email"`
	Password    string `json:"password"`
	DisplayName string `json:"display_name"`
}

type verifyRequest struct {
	Email string `json:"email"`
	Code  string `json:"code"`
}

type resendRequest struct {
	Email string `json:"email"`
}

type loginRequest struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}

// messageResponse is the generic acknowledgement body for endpoints that
// must not reveal more than "request accepted" (spec 9.1).
type messageResponse struct {
	Message string `json:"message"`
}

// sessionResponse never carries the refresh token: that only ever travels in
// the httpOnly cookie, so it is unreachable from JavaScript.
type sessionResponse struct {
	AccessToken string `json:"access_token"`
	ExpiresIn   int    `json:"expires_in_seconds"`
}

type userResponse struct {
	ID            string    `json:"id"`
	Email         string    `json:"email"`
	DisplayName   string    `json:"display_name"`
	EmailVerified bool      `json:"email_verified"`
	CreatedAt     time.Time `json:"created_at"`
}

func newUserResponse(u *domain.User) userResponse {
	return userResponse{
		ID:            u.ID.String(),
		Email:         u.Email,
		DisplayName:   u.DisplayName,
		EmailVerified: u.EmailVerified,
		CreatedAt:     u.CreatedAt,
	}
}
