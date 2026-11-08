package signerapi

import "strings"

type cookieKey struct {
	name   string
	domain string
	path   string
}

func keyOf(cookie Cookie) cookieKey {
	return cookieKey{
		name:   cookie.Name,
		domain: strings.ToLower(cookie.Domain),
		path:   cookie.Path,
	}
}

func cookieEqual(left, right Cookie) bool {
	return left.Value == right.Value &&
		left.Expires == right.Expires &&
		left.HTTPOnly == right.HTTPOnly &&
		left.Secure == right.Secure &&
		left.SameSite == right.SameSite &&
		left.PartitionKey == right.PartitionKey
}

// DiffCookies 返回新增/变化的 Cookie 与已删除 Cookie 的墓碑项。
//
// 墓碑项 value 为空、expires 为 0（Unix 纪元），供调用方按空值或过期时间
// 清理本地副本。domain 比较大小写不敏感，name/path 保持原样。
func DiffCookies(before, after []Cookie) (updated, deleted []Cookie) {
	updated = make([]Cookie, 0)
	deleted = make([]Cookie, 0)
	previous := make(map[cookieKey]Cookie, len(before))
	for _, cookie := range before {
		previous[keyOf(cookie)] = cookie
	}
	current := make(map[cookieKey]struct{}, len(after))
	for _, cookie := range after {
		key := keyOf(cookie)
		current[key] = struct{}{}
		old, ok := previous[key]
		if ok && cookieEqual(old, cookie) {
			continue
		}
		updated = append(updated, cookie)
	}
	for key, cookie := range previous {
		if _, ok := current[key]; ok {
			continue
		}
		deleted = append(deleted, Cookie{
			Name:     cookie.Name,
			Value:    "",
			Domain:   cookie.Domain,
			Path:     cookie.Path,
			Expires:  0,
			HTTPOnly: cookie.HTTPOnly,
			Secure:   cookie.Secure,
			SameSite: cookie.SameSite,
		})
	}
	return updated, deleted
}
