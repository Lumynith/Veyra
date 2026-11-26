"""Run a saved Python script in the sandbox (see automation/sandbox.py)."""
from automation.sandbox import PERMISSIONS, parse_inputs, run_script
from automation.script_library import ScriptLibrary
from blocks.base_block import Block, Param
from blocks.registry import register

PERMISSION_PARAMS = tuple(Param(f"allow_{name}", f"Allow: {name}", "bool", False, tooltip=text)
                          for name, text in PERMISSIONS.items())


def permissions(p: dict) -> dict[str, bool]:
    return {name: bool(p.get(f"allow_{name}")) for name in PERMISSIONS}


def store_outputs(ctx, prefix: str, outputs: dict) -> None:
    prefix = prefix.strip()
    for key, value in outputs.items():
        ctx.set(f"{prefix}_{key}" if prefix else key, value)


@register
class RunScript(Block):
    type_id, title, category, icon = "script.run", "Run Python Script", "Scripting", "script"
    description = ("Run a script saved in the Script Editor, in a restricted separate process. Values it sets "
                   "with output(name, value) become workflow variables.")
    params = (Param("script", "Script", "script", "", tooltip="A script saved in the Script Editor"),
              Param("inputs", "Inputs", "str", "", tooltip="name=value; other={variable} — read in the script as "
                                                           "inputs['name']"),
              Param("timeout", "Timeout (s)", "float", 30.0, minimum=0, maximum=86400, tooltip="0 = no limit"),
              ) + PERMISSION_PARAMS + (
              Param("output", "Output prefix", "var", "",
                    tooltip="Blank: outputs keep their own names. Otherwise they become {prefix}_{name}"),
              Param("source", "Source", "hidden", ""),)  # set by the Script Editor's Run; not shown
    summary_fmt = "{script}"

    def execute(self, ctx, p, node):
        source = p["source"] or ScriptLibrary(ctx.path("scripts")).read(p["script"])
        outputs = run_script(ctx, source, parse_inputs(p["inputs"]), permissions(p), p["timeout"],
                             on_print=lambda text: ctx.emit("script.output", text))
        store_outputs(ctx, p["output"], outputs)
        ctx.emit("script.result", outputs)
        return True
