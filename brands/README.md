# Hearth brand assets for Home Assistant

HA doesn't read integration icons from the integration itself — it fetches them
from https://brands.home-assistant.io, backed by the
[home-assistant/brands](https://github.com/home-assistant/brands) repo.

To get the Ember mark showing next to "Hearth" in HA's UI (integrations list,
devices, config flow):

1. Fork home-assistant/brands
2. Copy `custom_integrations/hearth/` from here into the fork's
   `custom_integrations/` directory (icon.png 256×256, icon@2x.png 512×512,
   logo + logo@2x same mark)
3. Open a PR — they merge custom-integration brand PRs routinely

Until that PR is merged HA shows a generic puzzle-piece icon; everything else
works normally.
