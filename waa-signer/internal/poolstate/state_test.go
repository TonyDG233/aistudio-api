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

func repeat(ch byte, count int) string {
	buf := make([]byte, count)
	for i := range buf {
		buf[i] = ch
	}
	return string(buf)
}
