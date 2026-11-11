package poolstate

import (
	"errors"
	"testing"
)

func TestDecideForceRecreate(t *testing.T) {
	cases := []struct {
		name      string
		hasWorker bool
		inFlight  int
		want      RecreateDecision
	}{
		{name: "no worker", hasWorker: false, inFlight: 0, want: ReuseWorker},
		{name: "worker but in flight", hasWorker: true, inFlight: 1, want: DeferUntilIdle},
		{name: "worker with many in flight", hasWorker: true, inFlight: 7, want: DeferUntilIdle},
		{name: "idle worker", hasWorker: true, inFlight: 0, want: CloseImmediately},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := DecideForceRecreate(tc.hasWorker, tc.inFlight); got != tc.want {
				t.Fatalf("DecideForceRecreate(%v, %d) = %v, want %v", tc.hasWorker, tc.inFlight, got, tc.want)
			}
		})
	}
}

func TestShouldRebuildAfterDrain(t *testing.T) {
	cases := []struct {
		pending  bool
		inFlight int
		want     bool
	}{
		{pending: false, inFlight: 0, want: false},
		{pending: true, inFlight: 3, want: false},
		{pending: true, inFlight: 0, want: true},
	}
	for _, tc := range cases {
		if got := ShouldRebuildAfterDrain(tc.pending, tc.inFlight); got != tc.want {
			t.Fatalf("ShouldRebuildAfterDrain(%v, %d) = %v, want %v", tc.pending, tc.inFlight, got, tc.want)
		}
	}
}

func TestGenerationLifecycle(t *testing.T) {
	var generation uint64
	generation = NextGeneration(generation)
	if generation != 1 {
		t.Fatalf("first generation = %d, want 1", generation)
	}
	generation = NextGeneration(generation)
	if generation != 2 {
		t.Fatalf("second generation = %d, want 2", generation)
	}
	if !ShouldClearPending(2, 2) {
		t.Fatal("latest generation must clear pending")
	}
	if ShouldClearPending(1, 2) {
		t.Fatal("stale generation must not clear pending")
	}
	if !ShouldClearPending(0, 2) {
		t.Fatal("generation 0 means unconditional clear")
	}
}

func TestResolveAccountKey(t *testing.T) {
	cases := []struct {
		name     string
		explicit string
		fallback string
		want     string
		wantErr  bool
	}{
		{name: "explicit wins", explicit: "acct_123-abc", fallback: "acc_deadbeef_u0", want: "acct_123-abc"},
		{name: "explicit trimmed", explicit: "  acct_123  ", fallback: "acc_deadbeef_u0", want: "acct_123"},
		{name: "fallback", explicit: "", fallback: "acc_deadbeef_u0", want: "acc_deadbeef_u0"},
		{name: "empty explicit uses fallback", explicit: "   ", fallback: "acc_deadbeef_u0", want: "acc_deadbeef_u0"},
		{name: "both empty", explicit: "", fallback: "", wantErr: true},
		{name: "path traversal", explicit: "../../etc/passwd", fallback: "x", wantErr: true},
		{name: "dot dot", explicit: "..", fallback: "x", wantErr: true},
		{name: "leading dot", explicit: ".hidden", fallback: "x", wantErr: true},
		{name: "slash", explicit: "acc/1", fallback: "x", wantErr: true},
		{name: "too long", explicit: repeat('a', 129), fallback: "x", wantErr: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := ResolveAccountKey(tc.explicit, tc.fallback)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("ResolveAccountKey(%q, %q) = %q, want error", tc.explicit, tc.fallback, got)
				}
				if !errors.Is(err, ErrInvalidAccountKey) && tc.explicit != "" {
					t.Fatalf("error %v should wrap ErrInvalidAccountKey", err)
				}
				return
			}
			if err != nil {
				t.Fatalf("ResolveAccountKey(%q, %q) unexpected error: %v", tc.explicit, tc.fallback, err)
			}
			if got != tc.want {
				t.Fatalf("ResolveAccountKey(%q, %q) = %q, want %q", tc.explicit, tc.fallback, got, tc.want)
			}
		})
	}
}

func TestExtractRotatingCredentials(t *testing.T) {
	cases := []struct {
		name    string
		cookies map[string]string
		want    map[string]string
	}{
		{name: "nil input", cookies: nil},
		{name: "empty input", cookies: map[string]string{}},
		{name: "only irrelevant cookies", cookies: map[string]string{"SID": "x", "SAPISID": "y"}},
		{
			name: "all rotating credentials",
			cookies: map[string]string{
				"__Secure-1PSIDTS": "a1",
				"__Secure-3PSIDTS": "a3",
				"SIDCC":            "cc",
				"__Secure-1PSIDCC": "cc1",
				"__Secure-3PSIDCC": "cc3",
			},
			want: map[string]string{
				"__Secure-1PSIDTS": "a1",
				"__Secure-3PSIDTS": "a3",
				"SIDCC":            "cc",
				"__Secure-1PSIDCC": "cc1",
				"__Secure-3PSIDCC": "cc3",
			},
		},
		{
			name: "partial and blank values dropped",
			cookies: map[string]string{
				"__Secure-1PSIDTS": "a1",
				"SIDCC":            "   ",
				"__Secure-3PSIDCC": "",
			},
			want: map[string]string{"__Secure-1PSIDTS": "a1"},
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := ExtractRotatingCredentials(tc.cookies)
			if len(got) != len(tc.want) {
				t.Fatalf("ExtractRotatingCredentials(%v) = %v, want %v", tc.cookies, got, tc.want)
			}
			for name, want := range tc.want {
				if got[name] != want {
					t.Fatalf("ExtractRotatingCredentials(%v)[%s] = %q, want %q", tc.cookies, name, got[name], want)
				}
			}
		})
	}
}

func TestHasClientRotatedCredentials(t *testing.T) {
	baseline := map[string]string{
		"__Secure-1PSIDTS": "old-1psidts",
		"__Secure-3PSIDTS": "old-3psidts",
		"SIDCC":            "old-sidcc",
	}
	cases := []struct {
		name     string
		baseline map[string]string
		incoming map[string]string
		want     bool
	}{
		{
			name:     "client unchanged",
			baseline: baseline,
			incoming: map[string]string{
				"__Secure-1PSIDTS": "old-1psidts",
				"__Secure-3PSIDTS": "old-3psidts",
				"SIDCC":            "old-sidcc",
			},
			want: false,
		},
		{
			name:     "client rotated 1PSIDTS",
			baseline: baseline,
			incoming: map[string]string{
				"__Secure-1PSIDTS": "new-1psidts",
				"__Secure-3PSIDTS": "old-3psidts",
				"SIDCC":            "old-sidcc",
			},
			want: true,
		},
		{
			name:     "incoming missing key does not misfire",
			baseline: baseline,
			incoming: map[string]string{"__Secure-3PSIDTS": "old-3psidts"},
			want:     false,
		},
		{
			name:     "incoming blank key does not misfire",
			baseline: baseline,
			incoming: map[string]string{"__Secure-1PSIDTS": "  ", "SIDCC": "old-sidcc"},
			want:     false,
		},
		{
			name:     "client rotated SIDCC only",
			baseline: baseline,
			incoming: map[string]string{"SIDCC": "new-sidcc"},
			want:     true,
		},
		{
			name:     "empty baseline never rotates",
			baseline: nil,
			incoming: map[string]string{"__Secure-1PSIDTS": "new-1psidts"},
			want:     false,
		},
		{
			name:     "baseline without rotating keys never rotates",
			baseline: map[string]string{"SID": "x"},
			incoming: map[string]string{"SID": "y"},
			want:     false,
		},
		{
			name:     "nil incoming never rotates",
			baseline: baseline,
			incoming: nil,
			want:     false,
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := HasClientRotatedCredentials(tc.baseline, tc.incoming); got != tc.want {
				t.Fatalf("HasClientRotatedCredentials(%v, %v) = %v, want %v", tc.baseline, tc.incoming, got, tc.want)
			}
		})
	}
}

func repeat(ch byte, count int) string {
	buf := make([]byte, count)
	for i := range buf {
		buf[i] = ch
	}
	return string(buf)
}
