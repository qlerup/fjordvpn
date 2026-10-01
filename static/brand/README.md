# FjordVPN identity

Original shield/F monogram with an endpoint dot, drawn as SVG for this project.
The shield represents the tunnel; the F and dot form the connection mark.

- `fjordvpn-mark.svg`: transparent mint symbol.
- `fjordvpn-mark-dark.svg`: transparent dark symbol for light backgrounds.
- `fjordvpn-icon.svg` and PNG sizes 16–1024: app/catalog icon on a dark tile.
- `fjordvpn-logo-light.*`: wordmark for dark backgrounds.
- `fjordvpn-logo-dark.*`: wordmark for light backgrounds.
- `favicon.ico`, 180px touch icon and 192/512px web manifest icons.

Primary mint: `#67dec2`; dark canvas: `#101c25`; light text: `#edf3f7`.
Keep the icon's proportions and clear space. Regenerate using
`python tools/build_brand.py` with Playwright and Pillow installed.
