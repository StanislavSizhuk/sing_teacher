// Package migrations embeds the goose SQL migrations into the api binary so a
// single static executable can apply its own schema on boot (spec 5.1, 18/E1).
package migrations

import "embed"

// FS holds every *.sql migration file, read by goose at startup.
//
//go:embed *.sql
var FS embed.FS
