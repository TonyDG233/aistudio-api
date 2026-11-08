module github.com/Mag1cFall/AIStudio2API

go 1.23

// Module language baseline: go 1.23 (Q-08).
// Upstream transport dependencies (uquic/utls/x-net) declare go 1.27 (x-net 1.26),
// so the effective build toolchain must be Go 1.27. Keep the directive below active:
// without it, a default Go 1.26.4 cannot resolve these dependencies.
toolchain go1.27.0

require (
	github.com/Mag1cFall/fhttp v0.6.8-firefox.1
	github.com/Mag1cFall/uquic v0.0.6-firefox.6
	github.com/Mag1cFall/utls v1.8.2-firefox.3
	github.com/bogdanfinn/tls-client v1.15.1
	github.com/bogdanfinn/utls v1.7.7-barnius
	github.com/brainplusplus/go-browserforge v0.1.0
	github.com/nlnwa/whatwg-url v0.6.2
	golang.org/x/net v0.59.0
	golang.org/x/text v0.42.0
)

require (
	github.com/andybalholm/brotli v1.2.0 // indirect
	github.com/bits-and-blooms/bitset v1.20.0 // indirect
	github.com/bogdanfinn/fhttp v0.6.8 // indirect
	github.com/klauspost/compress v1.20.1 // indirect
	github.com/molecule-man/go-brrr v1.2.0 // indirect
	golang.org/x/crypto v0.57.0 // indirect
	golang.org/x/sys v0.48.0 // indirect
)
