package main

import (
	"context"
	"net"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestParseDNSServers(t *testing.T) {
	tests := []struct {
		name string
		raw  string
		want []string
	}{
		{
			name: "empty",
			raw:  "  \t , ; \n ",
			want: nil,
		},
		{
			name: "mixed separators and default port",
			raw:  "8.8.8.8, 1.1.1.1;223.5.5.5\t9.9.9.9:5353",
			want: []string{"8.8.8.8:53", "1.1.1.1:53", "223.5.5.5:53", "9.9.9.9:5353"},
		},
		{
			name: "ipv6 bare and bracketed",
			raw:  "2001:4860:4860::8888, [2606:4700:4700::1111], [2001:4860:4860::8844]:5353",
			want: []string{"[2001:4860:4860::8888]:53", "[2606:4700:4700::1111]:53", "[2001:4860:4860::8844]:5353"},
		},
		{
			name: "deduplicate after port normalization",
			raw:  "8.8.8.8, 8.8.8.8:53; 1.1.1.1 1.1.1.1:53",
			want: []string{"8.8.8.8:53", "1.1.1.1:53"},
		},
		{
			name: "filter malformed urls and invalid ports",
			raw:  "http://8.8.8.8, https://1.1.1.1:53, :53, 8.8.8.8:0, 8.8.8.8:99999, 8.8.8.8:-1, 8.8.8.8:+53, 8.8.8.8:abc, 8.8.8.8/dns-query, [bad::ip]:53, 1.1.1.1:5353",
			want: []string{"1.1.1.1:5353"},
		},
		{
			name: "all invalid returns nil",
			raw:  "http://8.8.8.8, 8.8.8.8:0, 8.8.8.8:99999",
			want: nil,
		},
		{
			name: "strip ipv6 zone id",
			raw:  "[fe80::1%wlan0]:53, fe80::1%eth0, [fe80::2%wlan0]:5353",
			want: []string{"[fe80::1]:53", "[fe80::2]:5353"},
		},
		{
			name: "accept valid hostname",
			raw:  "dns.google, dns.quad9.net:5353",
			want: []string{"dns.google:53", "dns.quad9.net:5353"},
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got := parseDNSServers(tc.raw)
			if !reflect.DeepEqual(got, tc.want) {
				t.Fatalf("parseDNSServers(%q) = %#v, want %#v", tc.raw, got, tc.want)
			}
		})
	}
}

func TestFirstNonEmptyEnv(t *testing.T) {
	t.Setenv("WAA_DNS_SERVERS", "")
	t.Setenv("AISTUDIO_DNS_SERVERS", " 1.1.1.1 ")
	if got := firstNonEmptyEnv("WAA_DNS_SERVERS", "AISTUDIO_DNS_SERVERS"); got != "1.1.1.1" {
		t.Fatalf("expected fallback to AISTUDIO_DNS_SERVERS, got %q", got)
	}

	t.Setenv("WAA_DNS_SERVERS", "8.8.8.8")
	if got := firstNonEmptyEnv("WAA_DNS_SERVERS", "AISTUDIO_DNS_SERVERS"); got != "8.8.8.8" {
		t.Fatalf("expected WAA_DNS_SERVERS priority, got %q", got)
	}

	t.Setenv("WAA_DNS_SERVERS", "   ")
	t.Setenv("AISTUDIO_DNS_SERVERS", "")
	if got := firstNonEmptyEnv("WAA_DNS_SERVERS", "AISTUDIO_DNS_SERVERS"); got != "" {
		t.Fatalf("expected empty string, got %q", got)
	}
}

func TestResolveRuntimeDNSServers(t *testing.T) {
	tmpDir := t.TempDir()
	missingResolv := filepath.Join(tmpDir, "missing-resolv.conf")
	emptyResolv := filepath.Join(tmpDir, "empty-resolv.conf")
	if err := os.WriteFile(emptyResolv, nil, 0o644); err != nil {
		t.Fatalf("write empty resolv: %v", err)
	}
	nonEmptyResolv := filepath.Join(tmpDir, "resolv.conf")
	if err := os.WriteFile(nonEmptyResolv, []byte("nameserver 127.0.0.53\n"), 0o644); err != nil {
		t.Fatalf("write resolv: %v", err)
	}

	t.Setenv("WAA_DNS_SERVERS", "")
	t.Setenv("AISTUDIO_DNS_SERVERS", "")

	wantDefault := []string{"8.8.8.8:53", "1.1.1.1:53", "223.5.5.5:53"}
	for _, goos := range []string{"linux", "android"} {
		if got := resolveRuntimeDNSServers(goos, "", missingResolv); !reflect.DeepEqual(got, wantDefault) {
			t.Fatalf("%s missing resolv.conf = %#v, want %#v", goos, got, wantDefault)
		}
		if got := resolveRuntimeDNSServers(goos, "", emptyResolv); !reflect.DeepEqual(got, wantDefault) {
			t.Fatalf("%s empty resolv.conf = %#v, want %#v", goos, got, wantDefault)
		}
		if got := resolveRuntimeDNSServers(goos, "", nonEmptyResolv); got != nil {
			t.Fatalf("%s non-empty resolv.conf = %#v, want nil", goos, got)
		}
		if got := resolveRuntimeDNSServers(goos, "9.9.9.9", nonEmptyResolv); !reflect.DeepEqual(got, []string{"9.9.9.9:53"}) {
			t.Fatalf("%s explicit dns = %#v", goos, got)
		}
	}

	for _, goos := range []string{"windows", "darwin"} {
		if got := resolveRuntimeDNSServers(goos, "", missingResolv); got != nil {
			t.Fatalf("%s without explicit dns = %#v, want nil", goos, got)
		}
		if got := resolveRuntimeDNSServers(goos, "1.0.0.1", missingResolv); !reflect.DeepEqual(got, []string{"1.0.0.1:53"}) {
			t.Fatalf("%s explicit dns = %#v", goos, got)
		}
	}

	t.Setenv("AISTUDIO_DNS_SERVERS", "223.6.6.6")
	if got := resolveRuntimeDNSServers("windows", "", missingResolv); !reflect.DeepEqual(got, []string{"223.6.6.6:53"}) {
		t.Fatalf("env fallback = %#v, want [223.6.6.6:53]", got)
	}
}

func TestResolveAndroidCertDir(t *testing.T) {
	tmpDir := t.TempDir()
	conscryptDir := filepath.Join(tmpDir, "conscrypt")
	legacyDir := filepath.Join(tmpDir, "legacy")
	if err := os.MkdirAll(conscryptDir, 0o755); err != nil {
		t.Fatalf("mkdir conscrypt: %v", err)
	}
	if err := os.MkdirAll(legacyDir, 0o755); err != nil {
		t.Fatalf("mkdir legacy: %v", err)
	}
	missingBundle := filepath.Join(tmpDir, "ca-certificates.crt")
	existingBundle := filepath.Join(tmpDir, "existing-ca.crt")
	if err := os.WriteFile(existingBundle, []byte("cert"), 0o644); err != nil {
		t.Fatalf("write bundle: %v", err)
	}

	candidates := []string{conscryptDir, legacyDir}
	wantJoined := conscryptDir + ":" + legacyDir

	if got := resolveAndroidCertDir("linux", "", "", missingBundle, candidates); got != wantJoined {
		t.Fatalf("linux missing bundle = %q, want %q", got, wantJoined)
	}
	if got := resolveAndroidCertDir("android", "", "", missingBundle, []string{filepath.Join(tmpDir, "missing"), legacyDir}); got != legacyDir {
		t.Fatalf("android single dir = %q, want %q", got, legacyDir)
	}
	if got := resolveAndroidCertDir("linux", "/custom/dir", "", missingBundle, candidates); got != "" {
		t.Fatalf("expected empty when SSL_CERT_DIR set, got %q", got)
	}
	if got := resolveAndroidCertDir("linux", "", "/custom/ca.pem", missingBundle, candidates); got != "" {
		t.Fatalf("expected empty when SSL_CERT_FILE set, got %q", got)
	}
	if got := resolveAndroidCertDir("linux", "", "", existingBundle, candidates); got != "" {
		t.Fatalf("expected empty when ca-certificates.crt exists, got %q", got)
	}
	if got := resolveAndroidCertDir("windows", "", "", missingBundle, candidates); got != "" {
		t.Fatalf("expected empty on windows, got %q", got)
	}
	if got := resolveAndroidCertDir("darwin", "", "", missingBundle, candidates); got != "" {
		t.Fatalf("expected empty on darwin, got %q", got)
	}
}

func TestNewCustomDNSResolverFallbackAndRoundRobin(t *testing.T) {
	// Allocate an unused local TCP port (immediately closed) and a live local TCP listener.
	deadLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen dead port: %v", err)
	}
	deadAddr := deadLn.Addr().String()
	_ = deadLn.Close()

	liveLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen live port: %v", err)
	}
	defer liveLn.Close()

	accepted := make(chan struct{}, 4)
	go func() {
		for {
			conn, err := liveLn.Accept()
			if err != nil {
				return
			}
			accepted <- struct{}{}
			_ = conn.Close()
		}
	}()

	resolver := newCustomDNSResolver([]string{deadAddr, liveLn.Addr().String()})
	if !resolver.PreferGo {
		t.Fatal("expected PreferGo to be true")
	}

	ctx := context.Background()
	for i := 0; i < 2; i++ {
		conn, err := resolver.Dial(ctx, "tcp", "ignored:53")
		if err != nil {
			t.Fatalf("dial attempt %d failed: %v", i, err)
		}
		_ = conn.Close()
		<-accepted
	}
}

func TestNewCustomDNSResolverEmptyServersNoPanic(t *testing.T) {
	for _, servers := range [][]string{nil, {}} {
		resolver := newCustomDNSResolver(servers)
		if !resolver.PreferGo {
			t.Fatal("expected PreferGo to be true")
		}
		conn, err := resolver.Dial(context.Background(), "udp", "example.com:53")
		if conn != nil {
			_ = conn.Close()
			t.Fatal("expected nil conn when no servers configured")
		}
		if err == nil || err.Error() != "no DNS servers configured" {
			t.Fatalf("expected 'no DNS servers configured' error, got %v", err)
		}
	}
}

func TestConfigureRuntimeNetworkSetsDefaultResolver(t *testing.T) {
	origResolver := net.DefaultResolver
	t.Cleanup(func() {
		net.DefaultResolver = origResolver
	})

	configureRuntimeNetwork("8.8.8.8, 1.1.1.1")
	if net.DefaultResolver == nil || !net.DefaultResolver.PreferGo || net.DefaultResolver.Dial == nil {
		t.Fatalf("expected custom PreferGo net.DefaultResolver, got %#v", net.DefaultResolver)
	}
}
