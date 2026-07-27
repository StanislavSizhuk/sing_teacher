package song_test

import (
	"errors"
	"os"
)

// errBoom is a generic sentinel used where a test only cares that an error
// propagated, not which one.
var errBoom = errors.New("boom")

func readDirNames(dir string) ([]string, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		names = append(names, e.Name())
	}
	return names, nil
}
