package mailer

import (
	"context"
	"testing"
	"time"

	"github.com/stretchr/testify/require"
)

func TestBuildMessage_ProducesExpectedHeadersAndBody(t *testing.T) {
	got := buildMessage("coach@example.com", "student@example.com", "Verify your account", "Your code is 123456.\r\n")

	want := "From: coach@example.com\r\n" +
		"To: student@example.com\r\n" +
		"Subject: Verify your account\r\n" +
		"MIME-Version: 1.0\r\n" +
		"Content-Type: text/plain; charset=UTF-8\r\n" +
		"\r\n" +
		"Your code is 123456.\r\n"

	require.Equal(t, want, string(got))
}

// The recipient address ends up in a raw SMTP header line; without this
// guard a "\r\nBcc: ..." payload would let a caller inject extra headers
// (spec 11.5: validate external input at the boundary).
func TestSendVerificationCode_RejectsCRLFInRecipient(t *testing.T) {
	// TEST-NET-3 (RFC 5737): reserved, non-routable, so a regression that
	// removed the guard would time out instead of silently succeeding.
	m := NewSMTPMailer("203.0.113.1", 25, "", "", "coach@example.com")
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	err := m.SendVerificationCode(ctx, "victim@example.com\r\nBcc: attacker@evil.com", "123456")

	require.Error(t, err)
	require.Contains(t, err.Error(), "invalid recipient address")
}

func TestSendVerificationCode_ContextAlreadyCanceled_ReturnsWithoutSending(t *testing.T) {
	m := NewSMTPMailer("203.0.113.1", 25, "", "", "coach@example.com")
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	err := m.SendVerificationCode(ctx, "student@example.com", "123456")

	require.ErrorIs(t, err, context.Canceled)
}
