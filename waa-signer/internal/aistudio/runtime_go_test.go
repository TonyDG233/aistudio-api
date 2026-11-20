package aistudio

import (
	"io"
	"net/http"
	"os"
	"path/filepath"
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

func TestLoadOrCreateVisitIDReusesSidecar(t *testing.T) {
	accountDir := filepath.Join(t.TempDir(), "acc")
	if err := os.MkdirAll(accountDir, 0o755); err != nil {
		t.Fatalf("mkdir account dir: %v", err)
	}
	storageState := filepath.Join(accountDir, "storage-state.json")
	if err := os.WriteFile(filepath.Join(accountDir, "visit-id"), []byte("v1_existing\n"), 0o600); err != nil {
		t.Fatalf("write visit-id: %v", err)
	}

	got, err := loadOrCreateVisitID(storageState)
	if err != nil {
		t.Fatalf("loadOrCreateVisitID: %v", err)
	}
	if got != "v1_existing" {
		t.Fatalf("visit id = %q, want %q", got, "v1_existing")
	}
}

func TestLoadOrCreateVisitIDGeneratesAndPersists(t *testing.T) {
	accountDir := filepath.Join(t.TempDir(), "acc")
	if err := os.MkdirAll(accountDir, 0o755); err != nil {
		t.Fatalf("mkdir account dir: %v", err)
	}
	storageState := filepath.Join(accountDir, "storage-state.json")

	first, err := loadOrCreateVisitID(storageState)
	if err != nil {
		t.Fatalf("loadOrCreateVisitID: %v", err)
	}
	if !strings.HasPrefix(first, "v1_") {
		t.Fatalf("visit id = %q, want v1_ prefix", first)
	}
	raw, err := os.ReadFile(filepath.Join(accountDir, "visit-id"))
	if err != nil {
		t.Fatalf("read visit-id: %v", err)
	}
	if string(raw) != first {
		t.Fatalf("sidecar = %q, want %q", string(raw), first)
	}

	second, err := loadOrCreateVisitID(storageState)
	if err != nil {
		t.Fatalf("reload visit id: %v", err)
	}
	if second != first {
		t.Fatalf("second visit id = %q, want %q", second, first)
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
