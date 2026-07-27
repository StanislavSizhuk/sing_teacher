package httptransport

import (
	"log/slog"
	"net"
	"net/http"
	"strings"
	"time"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/service/auth"
)

const (
	refreshCookieName = "refresh_token"
	refreshCookiePath = "/api/v1/auth"

	googleStateCookieName    = "google_oauth_state"
	googleVerifierCookieName = "google_oauth_verifier"
	googleCookiePath         = "/api/v1/auth/google"
	oauthFlowTTL             = 5 * time.Minute
)

// Handler serves every /api/v1/auth/* and /api/v1/me endpoint.
type Handler struct {
	svc             *auth.Service
	logger          *slog.Logger
	cookieSecure    bool
	appBaseURL      string
	accessTokenTTL  time.Duration
	refreshTokenTTL time.Duration
}

// NewHandler builds a Handler. appBaseURL is where the browser is sent after
// a successful Google login (spec 9: the callback never puts tokens in a URL;
// the SPA calls /auth/refresh on load using the httpOnly cookie instead).
func NewHandler(svc *auth.Service, logger *slog.Logger, cookieSecure bool, appBaseURL string, accessTokenTTL, refreshTokenTTL time.Duration) *Handler {
	return &Handler{
		svc:             svc,
		logger:          logger,
		cookieSecure:    cookieSecure,
		appBaseURL:      appBaseURL,
		accessTokenTTL:  accessTokenTTL,
		refreshTokenTTL: refreshTokenTTL,
	}
}

// Every http.SetCookie call below carries a trailing #nosec G124: gosec can't
// see that Secure is intentionally h.cookieSecure rather than a literal
// `true` (dev runs over plain HTTP, so Secure must be off there); HttpOnly
// and SameSite are always set explicitly.

func (h *Handler) setRefreshCookie(w http.ResponseWriter, token string) {
	http.SetCookie(w, &http.Cookie{ // #nosec G124
		Name:     refreshCookieName,
		Value:    token,
		Path:     refreshCookiePath,
		HttpOnly: true,
		Secure:   h.cookieSecure,
		SameSite: http.SameSiteStrictMode,
		MaxAge:   int(h.refreshTokenTTL.Seconds()),
	})
}

func (h *Handler) clearRefreshCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{ // #nosec G124
		Name:     refreshCookieName,
		Value:    "",
		Path:     refreshCookiePath,
		HttpOnly: true,
		Secure:   h.cookieSecure,
		SameSite: http.SameSiteStrictMode,
		MaxAge:   -1,
	})
}

// setOAuthCookie stashes short-lived CSRF/PKCE state for the Google flow.
// SameSite=Lax (not Strict) is required here: Google's redirect back to our
// callback is a cross-site top-level navigation, which Strict cookies never
// ride along on.
func (h *Handler) setOAuthCookie(w http.ResponseWriter, name, value string) {
	http.SetCookie(w, &http.Cookie{ // #nosec G124
		Name:     name,
		Value:    value,
		Path:     googleCookiePath,
		HttpOnly: true,
		Secure:   h.cookieSecure,
		SameSite: http.SameSiteLaxMode,
		MaxAge:   int(oauthFlowTTL.Seconds()),
	})
}

func (h *Handler) clearOAuthCookies(w http.ResponseWriter) {
	for _, name := range []string{googleStateCookieName, googleVerifierCookieName} {
		http.SetCookie(w, &http.Cookie{ // #nosec G124
			Name: name, Value: "", Path: googleCookiePath,
			HttpOnly: true, Secure: h.cookieSecure, SameSite: http.SameSiteLaxMode, MaxAge: -1,
		})
	}
}

// clientIP trusts X-Forwarded-For because Caddy -- the only public ingress
// (spec 5.3) -- is the sole path to this service; it is a brute-force signal,
// not a hard security boundary, so a spoofed value only ever costs us a
// slightly wrong throttle bucket, never an auth bypass.
func clientIP(r *http.Request) string {
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		if first, _, ok := strings.Cut(xff, ","); ok {
			return strings.TrimSpace(first)
		}
		return strings.TrimSpace(xff)
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

// Register handles POST /auth/register: email+password sign-up.
func (h *Handler) Register(w http.ResponseWriter, r *http.Request) {
	req, err := decodeJSON[registerRequest](r)
	if err != nil {
		badRequest(w, r, "request body must be valid JSON")
		return
	}
	email, err := validateEmail(req.Email)
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}
	password, err := validatePassword(req.Password)
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}
	displayName, err := validateDisplayName(req.DisplayName)
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}

	if err := h.svc.Register(r.Context(), email, password, displayName); err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	writeJSON(w, http.StatusAccepted, messageResponse{
		Message: "If this email can be registered, a verification code has been sent.",
	})
}

// Verify handles POST /auth/verify: confirms an email with its 6-digit code.
func (h *Handler) Verify(w http.ResponseWriter, r *http.Request) {
	req, err := decodeJSON[verifyRequest](r)
	if err != nil {
		badRequest(w, r, "request body must be valid JSON")
		return
	}
	email, err := validateEmail(req.Email)
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}
	code, err := validateVerificationCode(req.Code)
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}

	if err := h.svc.VerifyEmail(r.Context(), email, code); err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, messageResponse{Message: "Email verified. You can now log in."})
}

// ResendVerification handles POST /auth/verify/resend.
func (h *Handler) ResendVerification(w http.ResponseWriter, r *http.Request) {
	req, err := decodeJSON[resendRequest](r)
	if err != nil {
		badRequest(w, r, "request body must be valid JSON")
		return
	}
	email, err := validateEmail(req.Email)
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}

	if err := h.svc.ResendVerification(r.Context(), email); err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, messageResponse{
		Message: "If this email exists and is not yet verified, a new code has been sent.",
	})
}

// Login handles POST /auth/login and sets the refresh cookie on success.
func (h *Handler) Login(w http.ResponseWriter, r *http.Request) {
	req, err := decodeJSON[loginRequest](r)
	if err != nil {
		badRequest(w, r, "request body must be valid JSON")
		return
	}
	email, err := validateEmail(req.Email)
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}
	password, err := validatePassword(req.Password)
	if err != nil {
		badRequest(w, r, err.Error())
		return
	}

	session, err := h.svc.Login(r.Context(), email, password, clientIP(r))
	if err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	h.setRefreshCookie(w, session.RefreshToken)
	writeJSON(w, http.StatusOK, sessionResponse{
		AccessToken: session.AccessToken,
		ExpiresIn:   int(h.accessTokenTTL.Seconds()),
	})
}

// Refresh handles POST /auth/refresh: rotates the refresh cookie and mints a
// new access token.
func (h *Handler) Refresh(w http.ResponseWriter, r *http.Request) {
	cookie, err := r.Cookie(refreshCookieName)
	if err != nil || cookie.Value == "" {
		writeServiceError(h.logger, w, r, domain.ErrRefreshTokenInvalid)
		return
	}

	session, err := h.svc.Refresh(r.Context(), cookie.Value)
	if err != nil {
		// Never leave a dead or compromised cookie sitting in the browser.
		h.clearRefreshCookie(w)
		writeServiceError(h.logger, w, r, err)
		return
	}
	h.setRefreshCookie(w, session.RefreshToken)
	writeJSON(w, http.StatusOK, sessionResponse{
		AccessToken: session.AccessToken,
		ExpiresIn:   int(h.accessTokenTTL.Seconds()),
	})
}

// Logout handles POST /auth/logout: revokes the current session only.
func (h *Handler) Logout(w http.ResponseWriter, r *http.Request) {
	if cookie, err := r.Cookie(refreshCookieName); err == nil && cookie.Value != "" {
		_ = h.svc.Logout(r.Context(), cookie.Value)
	}
	h.clearRefreshCookie(w)
	writeJSON(w, http.StatusOK, messageResponse{Message: "Logged out."})
}

// GoogleStart handles GET /auth/google: redirects to Google's consent screen.
func (h *Handler) GoogleStart(w http.ResponseWriter, r *http.Request) {
	authURL, state, verifier, err := h.svc.GoogleAuthStart(r.Context())
	if err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	h.setOAuthCookie(w, googleStateCookieName, state)
	h.setOAuthCookie(w, googleVerifierCookieName, verifier)
	http.Redirect(w, r, authURL, http.StatusFound)
}

// GoogleCallback handles GET /auth/google/callback.
func (h *Handler) GoogleCallback(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()
	code := query.Get("code")
	gotState := query.Get("state")

	stateCookie, stateErr := r.Cookie(googleStateCookieName)
	verifierCookie, verifierErr := r.Cookie(googleVerifierCookieName)
	h.clearOAuthCookies(w)

	if code == "" || stateErr != nil || verifierErr != nil {
		writeServiceError(h.logger, w, r, domain.ErrOAuthState)
		return
	}

	session, err := h.svc.GoogleCallback(r.Context(), code, verifierCookie.Value, gotState, stateCookie.Value)
	if err != nil {
		writeServiceError(h.logger, w, r, err)
		return
	}
	h.setRefreshCookie(w, session.RefreshToken)
	http.Redirect(w, r, h.appBaseURL, http.StatusFound)
}
