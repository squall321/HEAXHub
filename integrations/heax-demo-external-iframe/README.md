# Demo · External Iframe

This integration demonstrates the `external_iframe` stack: HEAXHub embeds an external
URL inside an `<iframe>` rendered in the portal shell. The remote site keeps its own
auth and uptime; HEAXHub just frames it.

## First page preview

When opened from the admin panel, this card resolves to:

> Target: <https://www.wikipedia.org/>
>
> Mode: `iframe`
>
> Sandbox: `allow-scripts allow-same-origin allow-forms`

```html
<iframe
  src="https://www.wikipedia.org/"
  sandbox="allow-scripts allow-same-origin allow-forms"
  loading="lazy"
  referrerpolicy="no-referrer"
></iframe>
```

## Heads-up: X-Frame-Options / CSP

Many sites refuse to be embedded by responding with
`X-Frame-Options: DENY | SAMEORIGIN` or `Content-Security-Policy: frame-ancestors 'none'`.
The browser then renders an empty frame and there is nothing HEAXHub can do about it.

Wikipedia is used here because it tolerates embedding for read-only browsing. Verify
your target's headers (`curl -I https://target.example.com/`) before publishing.

If the target blocks iframes, fall back to:

- `external_link` — opens in a new tab, no headers required.
- `external_proxy` — Caddy strips frame-blocking headers on the way through (use only
  for sites you control).

## Sandbox tokens used

| Token                 | Why                                         |
| --------------------- | ------------------------------------------- |
| `allow-scripts`       | Wikipedia search needs JS                   |
| `allow-same-origin`   | Cookies / `wikipedia.org` API calls         |
| `allow-forms`         | Search box submission                       |

Deliberately omitted: `allow-top-navigation`, `allow-popups`,
`allow-popups-to-escape-sandbox`, `allow-modals`. Add only if your target needs them.
