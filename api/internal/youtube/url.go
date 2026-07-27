// Package youtube fetches metadata and downloads audio via yt-dlp, invoked
// as argument lists through sysproc.Runner (spec 11.3). Enabling this import
// path at all is gated by FEATURE_YOUTUBE_IMPORT at the service layer (spec 11.4).
package youtube

import (
	"errors"
	"net/url"
	"strings"
)

// ErrInvalidURL means the input is not an https youtube.com/youtu.be link.
var ErrInvalidURL = errors.New("not a youtube url")

// allowedHosts restricts yt-dlp -- which itself understands hundreds of
// sites -- to YouTube only, so this ingestion path can never become a
// generic URL-fetch oracle for an attacker-supplied host (spec 11.3/11.5).
var allowedHosts = map[string]bool{
	"youtube.com":       true,
	"www.youtube.com":   true,
	"m.youtube.com":     true,
	"music.youtube.com": true,
	"youtu.be":          true,
}

// ValidateURL normalizes raw and rejects anything that is not an https
// YouTube link. The exact (not suffix) host match means a lookalike like
// "youtube.com.evil.example" is rejected, not accepted.
func ValidateURL(raw string) (string, error) {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Scheme != "https" || !allowedHosts[strings.ToLower(u.Host)] {
		return "", ErrInvalidURL
	}
	return u.String(), nil
}
