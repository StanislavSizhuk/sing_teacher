package auth_test

import (
	"context"
	"time"

	"github.com/google/uuid"

	"ai-vocal-coach/api/internal/domain"
	"ai-vocal-coach/api/internal/service/auth"
)

// --- fakeUserRepository -----------------------------------------------------

type fakeUserRepository struct {
	byID      map[uuid.UUID]*domain.User
	byEmail   map[string]uuid.UUID
	byGoogle  map[string]uuid.UUID
	createErr error // consumed once, then cleared: simulates a one-shot race

	getByEmailCalls int
}

func newFakeUserRepository() *fakeUserRepository {
	return &fakeUserRepository{
		byID:     map[uuid.UUID]*domain.User{},
		byEmail:  map[string]uuid.UUID{},
		byGoogle: map[string]uuid.UUID{},
	}
}

func cloneUser(u *domain.User) *domain.User {
	cp := *u
	return &cp
}

func (f *fakeUserRepository) Create(_ context.Context, u *domain.User) error {
	if f.createErr != nil {
		err := f.createErr
		f.createErr = nil
		return err
	}
	if _, exists := f.byEmail[u.Email]; exists {
		return domain.ErrEmailTaken
	}
	u.CreatedAt = time.Now()
	f.byID[u.ID] = cloneUser(u)
	f.byEmail[u.Email] = u.ID
	if u.GoogleID != nil {
		f.byGoogle[*u.GoogleID] = u.ID
	}
	return nil
}

func (f *fakeUserRepository) GetByEmail(_ context.Context, email string) (*domain.User, error) {
	f.getByEmailCalls++
	id, ok := f.byEmail[email]
	if !ok {
		return nil, domain.ErrNotFound
	}
	return cloneUser(f.byID[id]), nil
}

func (f *fakeUserRepository) GetByID(_ context.Context, id uuid.UUID) (*domain.User, error) {
	u, ok := f.byID[id]
	if !ok {
		return nil, domain.ErrNotFound
	}
	return cloneUser(u), nil
}

func (f *fakeUserRepository) GetByGoogleID(_ context.Context, googleID string) (*domain.User, error) {
	id, ok := f.byGoogle[googleID]
	if !ok {
		return nil, domain.ErrNotFound
	}
	return cloneUser(f.byID[id]), nil
}

func (f *fakeUserRepository) UpdateVerificationCode(_ context.Context, userID uuid.UUID, codeHash string, expiresAt time.Time) error {
	u, ok := f.byID[userID]
	if !ok {
		return domain.ErrNotFound
	}
	u.VerificationCodeHash = &codeHash
	u.VerificationExpiresAt = &expiresAt
	u.VerificationAttempts = 0
	return nil
}

func (f *fakeUserRepository) IncrementVerificationAttempts(_ context.Context, userID uuid.UUID) (int, error) {
	u, ok := f.byID[userID]
	if !ok {
		return 0, domain.ErrNotFound
	}
	u.VerificationAttempts++
	return u.VerificationAttempts, nil
}

func (f *fakeUserRepository) MarkVerified(_ context.Context, userID uuid.UUID) error {
	u, ok := f.byID[userID]
	if !ok {
		return domain.ErrNotFound
	}
	u.EmailVerified = true
	u.VerificationCodeHash = nil
	u.VerificationExpiresAt = nil
	u.VerificationAttempts = 0
	return nil
}

func (f *fakeUserRepository) LinkGoogleID(_ context.Context, userID uuid.UUID, googleID string) error {
	u, ok := f.byID[userID]
	if !ok {
		return domain.ErrNotFound
	}
	u.GoogleID = &googleID
	f.byGoogle[googleID] = userID
	return nil
}

func (f *fakeUserRepository) HardDelete(_ context.Context, userID uuid.UUID) error {
	u, ok := f.byID[userID]
	if !ok {
		return domain.ErrNotFound
	}
	delete(f.byID, userID)
	delete(f.byEmail, u.Email)
	if u.GoogleID != nil {
		delete(f.byGoogle, *u.GoogleID)
	}
	return nil
}

func (f *fakeUserRepository) SoftDeleteExpiredUnverified(_ context.Context) (int64, error) {
	var count int64
	now := time.Now()
	for _, u := range f.byID {
		if !u.EmailVerified && u.DeletedAt == nil && u.VerificationExpiresAt != nil && u.VerificationExpiresAt.Before(now) {
			deletedAt := now
			u.DeletedAt = &deletedAt
			count++
		}
	}
	return count, nil
}

// --- fakeRefreshTokenStore ---------------------------------------------------

type fakeRefreshTokenStore struct {
	issueFunc  func(ctx context.Context, userID uuid.UUID) (string, error)
	rotateFunc func(ctx context.Context, token string) (string, uuid.UUID, error)

	revoked      []string
	revokedUsers []uuid.UUID
}

func (f *fakeRefreshTokenStore) Issue(ctx context.Context, userID uuid.UUID) (string, error) {
	if f.issueFunc != nil {
		return f.issueFunc(ctx, userID)
	}
	return "refresh-" + userID.String(), nil
}

func (f *fakeRefreshTokenStore) Rotate(ctx context.Context, token string) (string, uuid.UUID, error) {
	if f.rotateFunc != nil {
		return f.rotateFunc(ctx, token)
	}
	return "rotated-" + token, uuid.New(), nil
}

func (f *fakeRefreshTokenStore) Revoke(_ context.Context, token string) error {
	f.revoked = append(f.revoked, token)
	return nil
}

func (f *fakeRefreshTokenStore) RevokeAllForUser(_ context.Context, userID uuid.UUID) error {
	f.revokedUsers = append(f.revokedUsers, userID)
	return nil
}

// --- fakeMailer ---------------------------------------------------------------

type sentEmail struct {
	To, Code string
}

type fakeMailer struct {
	sent []sentEmail
	err  error
}

func (f *fakeMailer) SendVerificationCode(_ context.Context, to, code string) error {
	if f.err != nil {
		return f.err
	}
	f.sent = append(f.sent, sentEmail{To: to, Code: code})
	return nil
}

// --- fakePasswordHasher ---------------------------------------------------------

const dummyHashValue = "hash:__dummy__"

// fakePasswordHasher avoids paying real argon2id cost in service-level tests;
// the hashing algorithm itself is covered by internal/security's own tests.
type fakePasswordHasher struct{}

func (fakePasswordHasher) Hash(password string) (string, error) { return "hash:" + password, nil }

func (fakePasswordHasher) Verify(password, hash string) (bool, error) {
	return "hash:"+password == hash, nil
}

func (fakePasswordHasher) DummyHash() string { return dummyHashValue }

// --- fakeAccessTokenIssuer ---------------------------------------------------

type fakeAccessTokenIssuer struct {
	issued []uuid.UUID
}

func (f *fakeAccessTokenIssuer) Issue(userID uuid.UUID) (string, error) {
	f.issued = append(f.issued, userID)
	return "access-" + userID.String(), nil
}

func (f *fakeAccessTokenIssuer) Parse(string) (uuid.UUID, error) {
	return uuid.Nil, domain.ErrInvalidAccessToken
}

// --- fakeLoginThrottle ---------------------------------------------------------

type fakeLoginThrottle struct {
	locked     bool
	retryAfter time.Duration
	checkErr   error

	failures int
	resets   int
}

func (f *fakeLoginThrottle) Check(context.Context, string) (bool, time.Duration, error) {
	if f.checkErr != nil {
		return false, 0, f.checkErr
	}
	return f.locked, f.retryAfter, nil
}

func (f *fakeLoginThrottle) RecordFailure(context.Context, string) error {
	f.failures++
	return nil
}

func (f *fakeLoginThrottle) Reset(context.Context, string) error {
	f.resets++
	return nil
}

// --- fakeVerificationThrottle -------------------------------------------------

type fakeVerificationThrottle struct {
	allowed           bool
	retryAfter        time.Duration
	dailyLimitReached bool
	calls             int
}

func (f *fakeVerificationThrottle) AllowResend(context.Context, uuid.UUID) (bool, time.Duration, bool, error) {
	f.calls++
	return f.allowed, f.retryAfter, f.dailyLimitReached, nil
}

// --- fakeGoogleVerifier ---------------------------------------------------------

type fakeGoogleVerifier struct {
	identity    auth.GoogleIdentity
	exchangeErr error

	authCodeURLCalls int
	exchangeCalls    int
}

func (f *fakeGoogleVerifier) AuthCodeURL(_ context.Context, state, _ string) (string, error) {
	f.authCodeURLCalls++
	return "https://accounts.google.com/o/oauth2/auth?state=" + state, nil
}

func (f *fakeGoogleVerifier) Exchange(context.Context, string, string) (auth.GoogleIdentity, error) {
	f.exchangeCalls++
	if f.exchangeErr != nil {
		return auth.GoogleIdentity{}, f.exchangeErr
	}
	return f.identity, nil
}

// --- fakeClock -------------------------------------------------------------

type fakeClock struct{ now time.Time }

func (f *fakeClock) Now() time.Time { return f.now }

// --- wiring -----------------------------------------------------------------

type testDeps struct {
	users          *fakeUserRepository
	tokens         *fakeRefreshTokenStore
	mailer         *fakeMailer
	access         *fakeAccessTokenIssuer
	loginThrottle  *fakeLoginThrottle
	verifyThrottle *fakeVerificationThrottle
	google         *fakeGoogleVerifier
	clock          *fakeClock
}

func newTestService() (*auth.Service, *testDeps) {
	d := &testDeps{
		users:          newFakeUserRepository(),
		tokens:         &fakeRefreshTokenStore{},
		mailer:         &fakeMailer{},
		access:         &fakeAccessTokenIssuer{},
		loginThrottle:  &fakeLoginThrottle{},
		verifyThrottle: &fakeVerificationThrottle{allowed: true},
		google:         &fakeGoogleVerifier{},
		clock:          &fakeClock{now: time.Now()},
	}
	svc := auth.NewService(d.users, d.tokens, d.mailer, fakePasswordHasher{}, d.access,
		d.loginThrottle, d.verifyThrottle, d.google, d.clock)
	return svc, d
}
