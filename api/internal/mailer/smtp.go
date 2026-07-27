// Package mailer implements the auth service's Mailer interface over SMTP.
package mailer

import (
	"context"
	"errors"
	"fmt"
	"net/smtp"
	"strings"
)

// SMTPMailer sends verification emails through an SMTP relay (a real one in
// production, mailhog in dev — see deploy/docker-compose.dev.yml).
type SMTPMailer struct {
	host, user, password, from string
	port                       int
}

// NewSMTPMailer builds a mailer for the given relay. user/password may be
// empty for relays that accept unauthenticated mail (e.g. mailhog).
func NewSMTPMailer(host string, port int, user, password, from string) *SMTPMailer {
	return &SMTPMailer{host: host, port: port, user: user, password: password, from: from}
}

// SendVerificationCode emails the 6-digit code to the given address.
//
// net/smtp predates context.Context and cannot cancel mid-dial; ctx is only
// checked before we start, which is enough to avoid sending after a caller
// has already given up.
func (m *SMTPMailer) SendVerificationCode(ctx context.Context, to, code string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if strings.ContainsAny(to, "\r\n") {
		return errors.New("invalid recipient address")
	}

	subject := "Verify your AI Vocal Coach account"
	body := fmt.Sprintf("Your verification code is %s. It expires in 24 hours.\r\n", code)
	msg := buildMessage(m.from, to, subject, body)

	var auth smtp.Auth
	if m.user != "" {
		auth = smtp.PlainAuth("", m.user, m.password, m.host)
	}

	addr := fmt.Sprintf("%s:%d", m.host, m.port)
	if err := smtp.SendMail(addr, auth, m.from, []string{to}, msg); err != nil {
		return fmt.Errorf("send verification email: %w", err)
	}
	return nil
}

func buildMessage(from, to, subject, body string) []byte {
	var b strings.Builder
	fmt.Fprintf(&b, "From: %s\r\n", from)
	fmt.Fprintf(&b, "To: %s\r\n", to)
	fmt.Fprintf(&b, "Subject: %s\r\n", subject)
	b.WriteString("MIME-Version: 1.0\r\n")
	b.WriteString("Content-Type: text/plain; charset=UTF-8\r\n")
	b.WriteString("\r\n")
	b.WriteString(body)
	return []byte(b.String())
}
