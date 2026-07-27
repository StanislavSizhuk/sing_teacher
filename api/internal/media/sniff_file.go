package media

import (
	"errors"
	"fmt"
	"io"
	"os"
)

// sniffBufferSize covers every supported format's magic bytes (spec 11.3);
// 512 bytes is generous headroom over the longest signature checked (the
// ISO base media "ftyp" box at offset 4-8).
const sniffBufferSize = 512

// SniffFile identifies path's audio format from its leading bytes, without
// trusting its filename or extension (spec 11.3).
func SniffFile(path string) (Format, bool, error) {
	f, err := os.Open(path) // #nosec G304 -- path is always a server-managed storage path, never raw user input
	if err != nil {
		return "", false, fmt.Errorf("open file for format sniffing: %w", err)
	}
	defer func() { _ = f.Close() }()

	buf := make([]byte, sniffBufferSize)
	n, err := f.Read(buf)
	if err != nil && !errors.Is(err, io.EOF) {
		return "", false, fmt.Errorf("read file for format sniffing: %w", err)
	}
	format, ok := Sniff(buf[:n])
	return format, ok, nil
}
