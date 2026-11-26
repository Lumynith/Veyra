"""Example developer plugin. Every .py file in plugins/ is loaded at startup.

Plugins are ordinary, trusted Python with full access to your computer: they are NOT sandboxed.
Only install plugins you have read or trust. (Scripts made in the Script Editor ARE sandboxed.)
"""
from blocks.base_block import BaseBlock, Param


class ShortNumber(BaseBlock):
    """Turn a number into short text: 1250 → 1.25K, 3400000 → 3.4M."""
    BLOCK_ID = "custom.example_short_number"
    DISPLAY_NAME = "Short Number (example plugin)"
    CATEGORY = "Custom"
    ICON = "variable"
    INPUTS = (Param("value", "Number", "str", "{gold}", tooltip="A number or {variable}"),
              Param("decimals", "Decimals", "int", 2, minimum=0, maximum=6))
    OUTPUT_VARIABLE = "short"

    def execute(self, context, inputs):
        value = float(str(inputs["value"]).replace(",", ""))
        for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
            if abs(value) >= limit:
                text = f"{value / limit:.{inputs['decimals']}f}".rstrip("0").rstrip(".") + suffix
                break
        else:
            text = f"{value:g}"
        context.log(f"{inputs['value']} → {text}")
        return {"success": True, "text": text}
