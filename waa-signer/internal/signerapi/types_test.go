package signerapi

import (
	"encoding/json"
	"testing"
)

func TestMintRequestAccountKeyContract(t *testing.T) {
	raw := []byte(`{
		"cookies": "SID=x; SAPISID=y",
		"authUser": 3,
		"g1Tier": "TIER1",
		"model": "models/gemini-3.7-flash",
		"prompt": "hi",
		"accountKey": "acct_stable_1"
	}`)
	var req MintRequest
	if err := json.Unmarshal(raw, &req); err != nil {
		t.Fatalf("unmarshal MintRequest: %v", err)
	}
	if req.AccountKey != "acct_stable_1" {
		t.Fatalf("accountKey = %q, want acct_stable_1", req.AccountKey)
	}
	if req.AuthUser != float64(3) {
		t.Fatalf("authUser = %#v, want 3", req.AuthUser)
	}
}

func TestSendRPCRequestAccountKeyContract(t *testing.T) {
	raw := []byte(`{
		"cookies": "SID=x; SAPISID=y",
		"authUser": "2",
		"url": "https://alkalimakersuite-pa.clients6.google.com/$rpc/x",
		"body": "[]",
		"headers": {"X-Test": "1"},
		"accountKey": "acct_stable_2"
	}`)
	var req SendRPCRequest
	if err := json.Unmarshal(raw, &req); err != nil {
		t.Fatalf("unmarshal SendRPCRequest: %v", err)
	}
	if req.AccountKey != "acct_stable_2" {
		t.Fatalf("accountKey = %q, want acct_stable_2", req.AccountKey)
	}
	if req.Headers["X-Test"] != "1" {
		t.Fatalf("headers not decoded: %#v", req.Headers)
	}
}

func TestMintResponseCookieContract(t *testing.T) {
	resp := MintResponse{
		OK:         true,
		Proof:      "proof",
		Digest:     "digest",
		AccountKey: "acct_stable_1",
		SetCookies: []Cookie{{
			Name: "__Secure-3PSIDTS", Value: "fresh", Domain: ".google.com", Path: "/",
			Expires: 1790000000, HTTPOnly: true, Secure: true, SameSite: "None",
		}},
		DeletedCookies: []Cookie{{
			Name: "OLD", Value: "", Domain: ".google.com", Path: "/", Expires: 0,
		}},
	}
	encoded, err := json.Marshal(resp)
	if err != nil {
		t.Fatalf("marshal MintResponse: %v", err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatalf("decode MintResponse: %v", err)
	}
	for _, field := range []string{"ok", "proof", "digest", "accountKey", "setCookies", "deletedCookies"} {
		if _, ok := decoded[field]; !ok {
			t.Fatalf("MintResponse JSON missing field %q: %s", field, encoded)
		}
	}
	setCookies, ok := decoded["setCookies"].([]any)
	if !ok || len(setCookies) != 1 {
		t.Fatalf("setCookies = %#v, want one item", decoded["setCookies"])
	}
	first := setCookies[0].(map[string]any)
	if first["name"] != "__Secure-3PSIDTS" || first["sameSite"] != "None" || first["httpOnly"] != true {
		t.Fatalf("unexpected cookie JSON: %#v", first)
	}
	deleted, ok := decoded["deletedCookies"].([]any)
	if !ok || len(deleted) != 1 {
		t.Fatalf("deletedCookies = %#v, want one item", decoded["deletedCookies"])
	}
	tombstone := deleted[0].(map[string]any)
	if tombstone["value"] != "" || tombstone["expires"] != float64(0) {
		t.Fatalf("deleted cookie must use empty value and zero expiry: %#v", tombstone)
	}
}

func TestHealthResponseContract(t *testing.T) {
	encoded, err := json.Marshal(HealthResponse{
		OK: true, Status: "ok", Engine: "spidermonkey-152", Workers: 1,
		IdleWorkers: 1, MaxWorkers: 8, Version: "2.1.0", Ready: true,
	})
	if err != nil {
		t.Fatalf("marshal HealthResponse: %v", err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatalf("decode HealthResponse: %v", err)
	}
	for _, field := range []string{"ok", "status", "engine", "workers", "idleWorkers", "maxWorkers", "version", "ready"} {
		if _, ok := decoded[field]; !ok {
			t.Fatalf("health JSON missing field %q: %s", field, encoded)
		}
	}
	for _, forbidden := range []string{"cookies", "cookie", "setCookies", "authorization"} {
		if _, ok := decoded[forbidden]; ok {
			t.Fatalf("health JSON must not contain %q: %s", forbidden, encoded)
		}
	}
}
