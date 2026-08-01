# Third-party visual assets

## Notionists avatars

- Original: Notionists by Zoish
- Integration: DiceBear Notionists SVG API, major version 10, exposed through
  the same-origin `/api/avatars/notionists/{seed}.svg` endpoint
- Source: https://www.dicebear.com/styles/notionists/
- Original download: https://heyzoish.gumroad.com/l/notionists
- License: CC0 1.0

The application derives a deterministic avatar seed only from each synthetic
profile identifier. It does not map region, income, age, policy preference, or
any other PGM attribute to appearance. The avatar is decorative and carries the
`decorative_synthetic` tag; it is not evidence and does not represent a real
person. Upstream SVGs are cached only under `project/data/avatar-cache`. If the
upstream service is unavailable or returns invalid content, the endpoint serves
an embedded neutral SVG so profile cards never degrade into broken images.
