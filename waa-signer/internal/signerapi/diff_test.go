package signerapi

import "testing"

func TestDiffCookiesRotation(t *testing.T) {
	before := []Cookie{{
		Name: "__Secure-3PSIDTS", Value: "old", Domain: ".google.com", Path: "/",
		Expires: 100, HTTPOnly: true, Secure: true, SameSite: "None",
	}}
	after := []Cookie{{
		Name: "__Secure-3PSIDTS", Value: "new", Domain: ".google.com", Path: "/",
		Expires: 200, HTTPOnly: true, Secure: true, SameSite: "None",
	}}
	updated, deleted := DiffCookies(before, after)
	if len(updated) != 1 || updated[0].Value != "new" {
		t.Fatalf("updated = %#v, want rotated cookie", updated)
	}
	if len(deleted) != 0 {
		t.Fatalf("deleted = %#v, want none", deleted)
	}
}

func TestDiffCookiesDeletionTombstone(t *testing.T) {
	before := []Cookie{
		{Name: "KEEP", Value: "same", Domain: ".google.com", Path: "/", Expires: 100},
		{Name: "DROP", Value: "gone", Domain: ".google.com", Path: "/", Expires: 100, HTTPOnly: true},
	}
	after := []Cookie{
		{Name: "KEEP", Value: "same", Domain: ".google.com", Path: "/", Expires: 100},
	}
	updated, deleted := DiffCookies(before, after)
	if len(updated) != 0 {
		t.Fatalf("updated = %#v, want none", updated)
	}
	if len(deleted) != 1 {
		t.Fatalf("deleted = %#v, want one tombstone", deleted)
	}
	tombstone := deleted[0]
	if tombstone.Name != "DROP" || tombstone.Value != "" || tombstone.Expires != 0 || !tombstone.HTTPOnly {
		t.Fatalf("unexpected tombstone: %#v", tombstone)
	}
}

func TestDiffCookiesDomainCaseInsensitive(t *testing.T) {
	before := []Cookie{{Name: "A", Value: "1", Domain: ".GOOGLE.COM", Path: "/", Expires: 5}}
	after := []Cookie{{Name: "A", Value: "1", Domain: ".google.com", Path: "/", Expires: 5}}
	updated, deleted := DiffCookies(before, after)
	if len(updated) != 0 || len(deleted) != 0 {
		t.Fatalf("case-only domain change must be a no-op: updated=%#v deleted=%#v", updated, deleted)
	}
}

func TestDiffCookiesEmptyInputsReturnEmptySlices(t *testing.T) {
	updated, deleted := DiffCookies(nil, nil)
	if updated == nil || deleted == nil {
		t.Fatalf("expected non-nil empty slices, got updated=%#v deleted=%#v", updated, deleted)
	}
}
