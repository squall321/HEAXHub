# Demo · External Link

This integration demonstrates the `external_link` stack: HEAXHub does **not** host this
application — it simply opens an external URL in a new browser tab when the user
clicks "Launch".

## First page preview

When opened from the admin panel, this card resolves to:

> Target: <https://github.com/koopark/HEAXHub>
>
> Mode: `new_tab`

```html
<a href="https://github.com/koopark/HEAXHub" target="_blank" rel="noopener noreferrer">
  Open HEAXHub on GitHub
</a>
```

## When to use this stack

- Bookmarking a SaaS dashboard (Grafana Cloud, Sentry, Datadog).
- Pinning an external monitoring or status page.
- Publishing public documentation that already lives elsewhere.

## What HEAXHub does (and does not) provide

| Concern              | HEAXHub           | External site |
| -------------------- | ----------------- | ------------- |
| Discovery & tagging  | Yes               | -             |
| Access control       | Visibility tag    | Own auth      |
| Build / run runtime  | None              | Owns          |
| Uptime / SLA         | Link only         | Owns          |

If you need single sign-on or to embed the page inline, see the
`external_iframe` or `external_proxy` demos instead.
