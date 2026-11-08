package upstreamguard

import (
	"errors"
	"testing"
)

func TestIsAllowedGoogleHost(t *testing.T) {
	allowed := []string{
		"google.com",
		"www.google.com",
		"aistudio.google.com",
		"alkalimakersuite-pa.clients6.google.com",
		"clients6.googleapis.com",
		"GOOGLEAPIS.COM",
		"google.com.",
	}
	for _, host := range allowed {
		if !IsAllowedGoogleHost(host) {
			t.Fatalf("IsAllowedGoogleHost(%q) = false, want true", host)
		}
	}
	rejected := []string{
		"",
		"evil.com",
		"google.com.evil.com",
		"evilgoogle.com",
		"googleapis.com.evil.com",
		"notgoogle.com",
		"google.com@evil.com",
		"127.0.0.1",
		"localhost",
	}
	for _, host := range rejected {
		if IsAllowedGoogleHost(host) {
			t.Fatalf("IsAllowedGoogleHost(%q) = true, want false", host)
		}
	}
}

func TestValidateTargetURL(t *testing.T) {
	valid := []string{
		"https://alkalimakersuite-pa.clients6.google.com/$rpc/google.internal.alkali.applications.makersuite.v1.MakerSuiteService/GenerateContent",
		"https://aistudio.google.com/",
		"https://clients6.googleapis.com:443/path?x=1",
	}
	for _, raw := range valid {
		if _, err := ValidateTargetURL(raw); err != nil {
			t.Fatalf("ValidateTargetURL(%q) unexpected error: %v", raw, err)
		}
	}

	invalid := []string{
		"",
		"http://google.com/",
		"ftp://google.com/",
		"https://evil.com/",
		"https://google.com.evil.com/",
		"https://evilgoogle.com/",
		"https://user:pass@google.com/",
		"https://google.com:8443/",
		"https://127.0.0.1/",
		"https://[::1]/",
		"//google.com/",
	}
	for _, raw := range invalid {
		if _, err := ValidateTargetURL(raw); err == nil {
			t.Fatalf("ValidateTargetURL(%q) = nil error, want rejection", raw)
		} else if !errors.Is(err, ErrInvalidTargetURL) {
			t.Fatalf("ValidateTargetURL(%q) error %v should wrap ErrInvalidTargetURL", raw, err)
		}
	}
}
