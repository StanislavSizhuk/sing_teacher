package httptransport

import (
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
)

// RouterDeps are everything NewRouter needs to wire the full API surface.
type RouterDeps struct {
	Auth         *Handler
	Health       *HealthHandler
	Logger       *slog.Logger
	CORSOrigin   string
	AccessParser AccessTokenParser
}

// NewRouter builds the complete chi router for the API: middleware chain,
// health checks, and every /api/v1 route implemented in E1.
func NewRouter(deps RouterDeps) http.Handler {
	r := chi.NewRouter()

	// Perimeter headers (HSTS, CSP, nosniff, ...) are Caddy's job at the edge
	// (deploy/Caddyfile) -- setting them here too would just duplicate the
	// header on every response. CORS stays here: it is API-contract-specific,
	// not a generic perimeter concern.
	r.Use(requestIDMiddleware)
	r.Use(recoverMiddleware(deps.Logger))
	r.Use(loggingMiddleware(deps.Logger))
	r.Use(corsMiddleware(deps.CORSOrigin))

	r.Get("/healthz", deps.Health.Healthz)
	r.Get("/readyz", deps.Health.Readyz)

	r.Route("/api/v1", func(r chi.Router) {
		r.Route("/auth", func(r chi.Router) {
			r.Post("/register", deps.Auth.Register)
			r.Post("/verify", deps.Auth.Verify)
			r.Post("/verify/resend", deps.Auth.ResendVerification)
			r.Post("/login", deps.Auth.Login)
			r.Post("/refresh", deps.Auth.Refresh)
			r.Post("/logout", deps.Auth.Logout)
			r.Get("/google", deps.Auth.GoogleStart)
			r.Get("/google/callback", deps.Auth.GoogleCallback)
		})

		r.Group(func(r chi.Router) {
			r.Use(authMiddleware(deps.Logger, deps.AccessParser))
			r.Get("/me", deps.Auth.GetMe)
			r.Delete("/me", deps.Auth.DeleteMe)
		})
	})

	return r
}
