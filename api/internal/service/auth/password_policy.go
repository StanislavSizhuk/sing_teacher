package auth

import (
	_ "embed"
	"strings"

	"ai-vocal-coach/api/internal/domain"
)

//go:embed common_passwords.txt
var commonPasswordsRaw string

var commonPasswords = buildCommonPasswordSet(commonPasswordsRaw)

func buildCommonPasswordSet(raw string) map[string]struct{} {
	lines := strings.Split(raw, "\n")
	set := make(map[string]struct{}, len(lines))
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		set[strings.ToLower(line)] = struct{}{}
	}
	return set
}

// minPasswordLength is the floor from spec 9.1.
const minPasswordLength = 10

// validatePasswordPolicy enforces spec 9.1: at least 10 characters, and not
// one of the most common leaked passwords. There is no "special character"
// rule by design -- per spec, character-class requirements push users toward
// predictable substitutions and measurably weaken real-world password
// strength rather than improving it.
func validatePasswordPolicy(password string) error {
	if len(password) < minPasswordLength {
		return domain.ErrWeakPassword
	}
	if _, common := commonPasswords[strings.ToLower(password)]; common {
		return domain.ErrWeakPassword
	}
	return nil
}
