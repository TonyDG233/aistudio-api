package proxydefault

import "testing"

func TestResolve(t *testing.T) {
	const discovered = "http://127.0.0.1:7890"
	tests := []struct {
		name             string
		explicit         bool
		value            string
		noProxyDiscovery bool
		want             string
		wantDiscover     int
	}{
		{
			name:         "default discovers proxy",
			want:         discovered,
			wantDiscover: 1,
		},
		{
			name:             "no-proxy-discovery skips discovery",
			noProxyDiscovery: true,
			want:             "",
			wantDiscover:     0,
		},
		{
			name:         "explicit proxy wins over discovery",
			explicit:     true,
			value:        "socks5://127.0.0.1:1080",
			want:         "socks5://127.0.0.1:1080",
			wantDiscover: 0,
		},
		{
			name:             "explicit proxy wins over no-proxy-discovery",
			explicit:         true,
			value:            "http://example.test:3128",
			noProxyDiscovery: true,
			want:             "http://example.test:3128",
			wantDiscover:     0,
		},
		{
			name:         "explicit empty proxy disables discovery",
			explicit:     true,
			value:        "",
			want:         "",
			wantDiscover: 0,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			discover := func() string {
				calls++
				return discovered
			}
			got := Resolve(tc.explicit, tc.value, tc.noProxyDiscovery, discover)
			if got != tc.want {
				t.Fatalf("Resolve(explicit=%v, value=%q, noDiscovery=%v) = %q, want %q",
					tc.explicit, tc.value, tc.noProxyDiscovery, got, tc.want)
			}
			if calls != tc.wantDiscover {
				t.Fatalf("discover invoked %d times, want %d", calls, tc.wantDiscover)
			}
		})
	}
}
