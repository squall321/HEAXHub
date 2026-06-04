# Demo · External Reverse Proxy

This integration demonstrates the `external_proxy` stack: HEAXHub places an internal
site behind `/apps/{id}/` via Caddy's `reverse_proxy`. End-users authenticate once
against HEAXHub, and Caddy forwards identity headers downstream — the internal site
does not need its own SSO.

## First page preview

When opened from the admin panel, this card resolves to:

> Path:      `https://heaxhub/apps/heax_demo_external_proxy/`
>
> Upstream:  `http://localhost:8126` (MailHog UI on this host)
>
> Strip prefix: `true`

```caddy
handle_path /apps/heax_demo_external_proxy/* {
    reverse_proxy http://localhost:8126 {
        header_up X-Forwarded-User  {http.auth.user.id}
        header_up X-Forwarded-Email {http.auth.user.email}
    }
}
```

## Why this is different from `external_iframe`

`external_iframe` just embeds a remote URL — the browser talks directly to that
origin and X-Frame-Options can break the embed. `external_proxy` puts Caddy in the
middle: requests look like same-origin HEAXHub traffic, X-Frame-Options stops
mattering, and identity headers can be injected.

Use this when:

- The upstream is on a private network only HEAXHub can reach.
- You want a single login for many internal tools.
- You need the URL bar to stay on the HEAXHub domain.
