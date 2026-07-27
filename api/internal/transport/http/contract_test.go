package httptransport_test

import (
	"log/slog"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/stretchr/testify/require"
	"gopkg.in/yaml.v3"

	httptransport "ai-vocal-coach/api/internal/transport/http"
)

// unprefixedPaths are the two path items that carry a path-level
// `servers: [{url: /}]` override in openapi.yaml, opting them out of the
// document's global /api/v1 prefix (health checks must stay unversioned).
var unprefixedPaths = map[string]bool{"/healthz": true, "/readyz": true}

// Path Item Objects can carry non-method siblings (servers, parameters,
// summary, ...), so values are decoded as `any` rather than a fixed shape.
type openAPIDoc struct {
	Paths map[string]map[string]any `yaml:"paths"`
}

var nonMethodKeys = map[string]bool{
	"servers": true, "parameters": true, "summary": true, "description": true, "$ref": true,
}

// specRoutes reads api/openapi.yaml and returns, for every declared
// operation, the fully-resolved path (with /api/v1 applied where it isn't
// overridden) mapped to its set of HTTP methods.
func specRoutes(t *testing.T) map[string]map[string]bool {
	t.Helper()
	data, err := os.ReadFile("../../../openapi.yaml")
	require.NoError(t, err)

	var doc openAPIDoc
	require.NoError(t, yaml.Unmarshal(data, &doc))

	routes := map[string]map[string]bool{}
	for p, methods := range doc.Paths {
		resolved := p
		if !unprefixedPaths[p] {
			resolved = "/api/v1" + p
		}
		set := make(map[string]bool, len(methods))
		for m := range methods {
			if nonMethodKeys[m] {
				continue
			}
			set[strings.ToUpper(m)] = true
		}
		routes[resolved] = set
	}
	return routes
}

// registeredRoutes introspects the live chi router built by NewRouter.
func registeredRoutes(t *testing.T) map[string]map[string]bool {
	t.Helper()
	router := httptransport.NewRouter(httptransport.RouterDeps{
		Auth:         httptransport.NewHandler(nil, slog.Default(), false, "https://example.com", 15*time.Minute, 720*time.Hour),
		Health:       httptransport.NewHealthHandler(nil, nil),
		Logger:       slog.Default(),
		CORSOrigin:   "https://example.com",
		AccessParser: nil,
	})

	chiRouter, ok := router.(chi.Router)
	require.True(t, ok, "NewRouter must return a chi.Router for introspection")

	routes := map[string]map[string]bool{}
	err := chi.Walk(chiRouter, func(method, route string, _ http.Handler, _ ...func(http.Handler) http.Handler) error {
		route = strings.TrimSuffix(route, "/")
		if routes[route] == nil {
			routes[route] = map[string]bool{}
		}
		routes[route][method] = true
		return nil
	})
	require.NoError(t, err)
	return routes
}

// TestOpenAPIContract_MatchesRegisteredRoutes guards against the two
// documents drifting apart (spec 15.1: contract tests validate handlers
// against openapi.yaml).
func TestOpenAPIContract_MatchesRegisteredRoutes(t *testing.T) {
	spec := specRoutes(t)
	registered := registeredRoutes(t)

	for path, methods := range spec {
		for method := range methods {
			require.Truef(t, registered[path][method],
				"openapi.yaml declares %s %s but no such route is registered in the router", method, path)
		}
	}

	for path, methods := range registered {
		for method := range methods {
			require.Truef(t, spec[path][method],
				"router serves %s %s but openapi.yaml does not document it", method, path)
		}
	}
}
