package aistudio

import (
	"io"
	"net/http"
	"strings"
	"testing"
)

func startupResponse(status int, body string) *http.Response {
	return &http.Response{
		StatusCode: status,
		Body:       io.NopCloser(strings.NewReader(body)),
	}
}

func TestCaptureStartupResponseKeepsTierAndModels(t *testing.T) {
	runtime := &goWAARuntime{visitID: "v1_test"}
	runtime.captureStartupResponse("GetAiStudioBenefitTier", startupResponse(http.StatusOK, "[2]"))
	runtime.captureStartupResponse("ListModels", startupResponse(http.StatusOK, `[[["models/gemini-x"]]]`))

	capture := runtime.StartupCapture()
	if capture.TierRaw != "[2]" {
		t.Fatalf("TierRaw = %q, want %q", capture.TierRaw, "[2]")
	}
	if capture.ModelsRaw != `[[["models/gemini-x"]]]` {
		t.Fatalf("ModelsRaw = %q", capture.ModelsRaw)
	}
	if capture.VisitID != "v1_test" {
		t.Fatalf("VisitID = %q", capture.VisitID)
	}
	if capture.CapturedAt.IsZero() {
		t.Fatal("CapturedAt must be set after a capture")
	}
}

func TestCaptureStartupResponseIsBounded(t *testing.T) {
	runtime := &goWAARuntime{}
	oversized := strings.Repeat("x", startupCaptureLimit+4096)
	runtime.captureStartupResponse("ListModels", startupResponse(http.StatusOK, oversized))
	capture := runtime.StartupCapture()
	if len(capture.ModelsRaw) != startupCaptureLimit {
		t.Fatalf("ModelsRaw length = %d, want %d", len(capture.ModelsRaw), startupCaptureLimit)
	}
}

func TestCaptureStartupResponseIgnoresErrorsAndOtherRPCs(t *testing.T) {
	runtime := &goWAARuntime{}
	runtime.captureStartupResponse("ListModels", startupResponse(http.StatusForbidden, "denied"))
	runtime.captureStartupResponse("GetUserPreferences", startupResponse(http.StatusOK, "{}"))

	capture := runtime.StartupCapture()
	if capture.TierRaw != "" || capture.ModelsRaw != "" {
		t.Fatalf("unexpected capture: %#v", capture)
	}
	if !capture.CapturedAt.IsZero() {
		t.Fatal("capturedAt must stay zero when nothing was captured")
	}
}
