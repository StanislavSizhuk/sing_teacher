package httptransport

import (
	"encoding/json"
	"io"
	"net/http"
)

// maxBodyBytes caps request bodies this layer will even attempt to decode.
// The real per-feature limits (audio uploads, etc.) belong to later stages;
// this is only a defensive ceiling for small JSON auth payloads.
const maxBodyBytes = 64 * 1024

func decodeJSON[T any](r *http.Request) (T, error) {
	var v T
	dec := json.NewDecoder(io.LimitReader(r.Body, maxBodyBytes))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&v); err != nil {
		return v, err
	}
	return v, nil
}

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func badRequest(w http.ResponseWriter, r *http.Request, detail string) {
	writeProblem(w, r, http.StatusBadRequest, "Bad Request", detail, "INVALID_REQUEST")
}
