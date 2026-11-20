package aistudio

import (
	"fmt"
	"slices"
	"testing"

	tls "github.com/bogdanfinn/utls"
)

// TestFirefox152ClientHelloSpec 锁定 firefox152ClientHello 的线上形状：16 条 cipher（不含 151/152 已禁用的
// 0xC009）、扩展顺序，以及 Version 标签。cipher 或扩展顺序被误改时该测试必须变红。
func TestFirefox152ClientHelloSpec(t *testing.T) {
	hello := firefox152ClientHello()
	if hello.Client != "Firefox" {
		t.Fatalf("Client = %q, want %q", hello.Client, "Firefox")
	}
	if hello.Version != "152" {
		t.Fatalf("Version = %q, want %q", hello.Version, "152")
	}

	spec, err := hello.SpecFactory()
	if err != nil {
		t.Fatalf("SpecFactory: %v", err)
	}

	wantCiphers := []uint16{
		tls.TLS_AES_128_GCM_SHA256,
		tls.TLS_CHACHA20_POLY1305_SHA256,
		tls.TLS_AES_256_GCM_SHA384,
		tls.TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,
		tls.TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,
		tls.TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256,
		tls.TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256,
		tls.TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384,
		tls.TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,
		tls.TLS_ECDHE_ECDSA_WITH_AES_256_CBC_SHA,
		tls.TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA,
		tls.TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA,
		tls.TLS_RSA_WITH_AES_128_GCM_SHA256,
		tls.TLS_RSA_WITH_AES_256_GCM_SHA384,
		tls.TLS_RSA_WITH_AES_128_CBC_SHA,
		tls.TLS_RSA_WITH_AES_256_CBC_SHA,
	}
	if !slices.Equal(spec.CipherSuites, wantCiphers) {
		t.Fatalf("cipher suites = %#v, want %#v", spec.CipherSuites, wantCiphers)
	}
	if slices.Contains(spec.CipherSuites, uint16(0xC009)) {
		t.Fatal("cipher 0xC009 must be absent: Firefox 151/152 disabled security.ssl3.ecdhe_ecdsa_aes_128_sha (Mozilla Bug 2035801)")
	}

	wantExtensions := []string{
		"*tls.SNIExtension",
		"*tls.ExtendedMasterSecretExtension",
		"*tls.RenegotiationInfoExtension",
		"*tls.SupportedCurvesExtension",
		"*tls.SupportedPointsExtension",
		"*tls.SessionTicketExtension",
		"*tls.ALPNExtension",
		"*tls.StatusRequestExtension",
		"*tls.FakeDelegatedCredentialsExtension",
		"*tls.SCTExtension",
		"*tls.KeyShareExtension",
		"*tls.SupportedVersionsExtension",
		"*tls.SignatureAlgorithmsExtension",
		"*tls.PSKKeyExchangeModesExtension",
		"*tls.FakeRecordSizeLimitExtension",
		"*tls.UtlsCompressCertExtension",
		"*tls.GREASEEncryptedClientHelloExtension",
	}
	gotExtensions := make([]string, len(spec.Extensions))
	for i, ext := range spec.Extensions {
		gotExtensions[i] = fmt.Sprintf("%T", ext)
	}
	if !slices.Equal(gotExtensions, wantExtensions) {
		t.Fatalf("extension order = %#v, want %#v", gotExtensions, wantExtensions)
	}
	if gotExtensions[0] != "*tls.SNIExtension" {
		t.Fatalf("first extension = %q, want *tls.SNIExtension", gotExtensions[0])
	}
	if last := gotExtensions[len(gotExtensions)-1]; last != "*tls.GREASEEncryptedClientHelloExtension" {
		t.Fatalf("last extension = %q, want *tls.GREASEEncryptedClientHelloExtension", last)
	}

	curves, ok := spec.Extensions[3].(*tls.SupportedCurvesExtension)
	if !ok {
		t.Fatalf("extensions[3] = %T, want *tls.SupportedCurvesExtension", spec.Extensions[3])
	}
	if !slices.Contains(curves.Curves, tls.X25519MLKEM768) {
		t.Fatalf("supported curves = %#v, want X25519MLKEM768 present", curves.Curves)
	}

	keyShares, ok := spec.Extensions[10].(*tls.KeyShareExtension)
	if !ok {
		t.Fatalf("extensions[10] = %T, want *tls.KeyShareExtension", spec.Extensions[10])
	}
	groups := make([]tls.CurveID, len(keyShares.KeyShares))
	for i, share := range keyShares.KeyShares {
		groups[i] = share.Group
	}
	if !slices.Contains(groups, tls.X25519MLKEM768) {
		t.Fatalf("key share groups = %#v, want X25519MLKEM768 present", groups)
	}
}
