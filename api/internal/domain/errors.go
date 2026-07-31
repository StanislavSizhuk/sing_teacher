package domain

import "errors"

// Sentinel errors returned by services and repositories. Transport maps each
// one to an HTTP status and a stable machine-readable code; never a raw
// error string.
var (
	// ErrNotFound means the requested resource does not exist (or is soft-deleted).
	ErrNotFound = errors.New("resource not found")

	// ErrEmailTaken means an active account already owns this email address.
	ErrEmailTaken = errors.New("email already registered")

	// ErrWeakPassword means the password failed the length/common-password policy (spec 9.1).
	ErrWeakPassword = errors.New("password does not meet policy")

	// ErrInvalidCredentials covers both an unknown email and a wrong password.
	// The two cases are deliberately not distinguished (spec 9.1: login must
	// not reveal whether an email exists).
	ErrInvalidCredentials = errors.New("invalid email or password")

	// ErrAccountLocked means the (email, IP) pair is in brute-force cooldown or lockout.
	ErrAccountLocked = errors.New("too many attempts, account temporarily locked")

	// ErrEmailNotVerified is only ever returned after a correct password
	// check, so revealing it does not leak account existence to a third party.
	ErrEmailNotVerified = errors.New("email not verified")

	// ErrVerificationCodeInvalid means the code does not match the stored hash.
	ErrVerificationCodeInvalid = errors.New("verification code is invalid")

	// ErrVerificationCodeExpired means verification_expires_at has passed.
	ErrVerificationCodeExpired = errors.New("verification code has expired")

	// ErrTooManyVerificationAttempts means the 5-attempt cap (spec 9.1) was hit; the code is dead.
	ErrTooManyVerificationAttempts = errors.New("too many verification attempts")

	// ErrVerificationCooldown means a resend was requested inside the 60s window (FR-04).
	ErrVerificationCooldown = errors.New("verification code was already sent recently")

	// ErrVerificationDailyLimit means the 5-per-day resend cap (FR-04) was reached.
	ErrVerificationDailyLimit = errors.New("daily verification resend limit reached")

	// ErrRefreshTokenInvalid means the token is unknown, expired, or already consumed.
	ErrRefreshTokenInvalid = errors.New("refresh token is invalid or expired")

	// ErrRefreshTokenReused means a token already rotated away was presented again,
	// which is treated as token-family compromise (spec 9.1).
	ErrRefreshTokenReused = errors.New("refresh token reuse detected")

	// ErrGoogleEmailNotVerified means Google reported the identity's email as unverified;
	// we refuse to trust it rather than silently creating an account we cannot confirm.
	ErrGoogleEmailNotVerified = errors.New("google account email is not verified")

	// ErrOAuthState means the OAuth callback state parameter did not match the
	// one issued at flow start (CSRF, spec 9.1).
	ErrOAuthState = errors.New("oauth state mismatch")

	// ErrInvalidAccessToken means the JWT is missing, malformed, expired, or
	// signed with a key we do not recognize.
	ErrInvalidAccessToken = errors.New("invalid or expired access token")

	// ErrUnsupportedAudioFormat means the uploaded bytes did not match any
	// supported audio format's magic bytes (spec 11.3 -- checked on content,
	// never on the filename extension).
	ErrUnsupportedAudioFormat = errors.New("unsupported audio format")

	// ErrAudioTooLarge means the audio exceeds MAX_UPLOAD_MB (spec NFR-05).
	ErrAudioTooLarge = errors.New("audio file is too large")

	// ErrAudioTooLong means the audio's duration exceeds MAX_AUDIO_SECONDS (spec NFR-05).
	ErrAudioTooLong = errors.New("audio is too long")

	// ErrYouTubeImportDisabled means FEATURE_YOUTUBE_IMPORT is off (spec 11.4).
	ErrYouTubeImportDisabled = errors.New("youtube import is disabled")

	// ErrInvalidYouTubeURL means the URL is not a youtube.com/youtu.be link.
	ErrInvalidYouTubeURL = errors.New("not a valid youtube url")

	// ErrYouTubeVideoTooLong means the video's duration (checked before
	// download, FR-12) exceeds MAX_AUDIO_SECONDS.
	ErrYouTubeVideoTooLong = errors.New("youtube video exceeds the duration limit")

	// ErrQueueFull means the analysis queue is at QUEUE_MAX_LENGTH (spec 10, FR-24).
	ErrQueueFull = errors.New("analysis queue is full")

	// ErrAnalysisRateLimited means the caller hit USER_ANALYSES_PER_HOUR (spec 10, FR-24).
	ErrAnalysisRateLimited = errors.New("too many analyses requested, try again later")

	// ErrAnalysisNotQueued means a cancel was requested for an analysis that
	// is no longer (or never was) in the queued state (FR-25).
	ErrAnalysisNotQueued = errors.New("analysis is not in the queued state")

	// ErrAnalysisNotFailed means a retry was requested for an analysis that
	// is not in the failed state (FR-26).
	ErrAnalysisNotFailed = errors.New("analysis is not in the failed state")

	// ErrReferencePrepFailed means the song's cold path (Demucs/Whisper/
	// reference pitch) failed, so an analysis against it cannot be queued
	// until the song is re-prepared (FR-17, spec 10).
	ErrReferencePrepFailed = errors.New("reference preparation failed for this song")

	// ErrSongPrepNotFailed means POST /songs/{id}/prepare was called for a
	// song whose prep_status is not 'failed' -- only a failed prep can be
	// restarted (FR-17).
	ErrSongPrepNotFailed = errors.New("song preparation is not in the failed state")
)
