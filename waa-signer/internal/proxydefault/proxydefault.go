// Package proxydefault decides the process-level default proxy for waa-signer.
//
// The signer auto-discovers a proxy from environment variables and well-known
// local ports when no explicit -proxy is supplied.  External exit mode needs
// a hard opt-out so a proxyless request can never silently inherit a host proxy.
package proxydefault

// Resolve decides the effective default proxy:
//   - an explicit -proxy (including an explicit empty string) always wins
//     and never triggers discovery;
//   - with -no-proxy-discovery and no explicit value the default stays empty
//     and discovery is never invoked;
//   - otherwise the historical discovery result is used.
//
// discover is injected so tests can prove it is skipped.
func Resolve(explicit bool, proxyValue string, noProxyDiscovery bool, discover func() string) string {
	if explicit {
		return proxyValue
	}
	if noProxyDiscovery {
		return ""
	}
	return discover()
}
