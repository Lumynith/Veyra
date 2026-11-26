"""Flow Control blocks: structure, loops, branches, variables, comments."""
import logging

from automation.engine import BreakLoop, EndWorkflow
from automation.expressions import OPERATORS, coerce, compare, evaluate, fmt
from blocks.base_block import Block, Param
from blocks.registry import register

CAT = "Flow Control"
CONDITION = (
    Param("left", "Value", "str", "{counter}", tooltip="Use {name} to insert a variable"),
    Param("op", "Operator", "choice", "==", options=OPERATORS),
    Param("right", "Compare to", "str", "0"),
    Param("case_sensitive", "Case sensitive", "bool", False),
)


def _condition(p) -> bool:
    return compare(p["left"], p["op"], p["right"], p["case_sensitive"])


@register
class Start(Block):
    type_id, title, category, icon = "flow.start", "Start", CAT, "flag"
    description = "The workflow runs from here, top to bottom."
    hat = True

    def execute(self, ctx, p, node):
        pass


@register
class End(Block):
    type_id, title, category, icon = "flow.end", "End", CAT, "end"
    description = "Finish the workflow successfully."
    cap = True

    def execute(self, ctx, p, node):
        raise EndWorkflow(stopped=False)


@register
class Wait(Block):
    type_id, title, category, icon = "flow.wait", "Wait", CAT, "clock"
    description = "Pause for a number of seconds."
    params = (Param("seconds", "Seconds", "float", 1.0, minimum=0, maximum=86400),)
    summary_fmt = "{seconds:g} s"

    def execute(self, ctx, p, node):
        ctx.wait(p["seconds"])


@register
class Repeat(Block):
    type_id, title, category, icon = "flow.repeat", "Repeat", CAT, "repeat"
    description = "Run the blocks inside a number of times (0 = forever)."
    container = True
    params = (Param("times", "Times", "int", 3, minimum=0, maximum=10**9, tooltip="0 repeats forever"),
              Param("counter", "Counter variable", "var", "", tooltip="Optional: set to 1, 2, 3… each pass"))
    summary_fmt = "{times} times"

    @classmethod
    def summary(cls, params):
        return "forever" if int(params.get("times", 0) or 0) <= 0 else f"{params['times']} times"

    def execute(self, ctx, p, node):
        i = 0
        try:
            while p["times"] <= 0 or i < p["times"]:
                i += 1
                if p["counter"]:
                    ctx.set(p["counter"], i)
                ctx.run_blocks(node["body"])
                if not node["body"]:
                    ctx.wait(0.01)  # an empty forever loop must not spin a CPU core
        except BreakLoop:
            pass


@register
class RepeatUntil(Block):
    type_id, title, category, icon = "flow.repeat_until", "Repeat Until", CAT, "repeat"
    description = "Run the blocks inside until the condition is true (checked before each pass)."
    container = True
    params = CONDITION
    summary_fmt = "{left} {op} {right}"

    def execute(self, ctx, p, node):
        try:
            while not _condition(ctx.resolve(self, node["params"])):  # re-read variables every pass
                ctx.run_blocks(node["body"])
                if not node["body"]:
                    ctx.wait(0.01)
        except BreakLoop:
            pass


@register
class If(Block):
    type_id, title, category, icon = "flow.if", "If", CAT, "branch"
    description = "Run the blocks inside when the condition is true. Follow with Else for the other case."
    container = True
    params = CONDITION
    summary_fmt = "{left} {op} {right}"

    def execute(self, ctx, p, node):
        ok = _condition(p)
        if ok:
            ctx.run_blocks(node["body"])
        return ok


@register
class Else(Block):
    type_id, title, category, icon = "flow.else", "Else", CAT, "branch"
    description = "Runs when the If directly above was false. (Handled by the engine.)"
    container = True

    def execute(self, ctx, p, node):  # the engine handles Else itself
        raise RuntimeError("Else must directly follow an If block")


@register
class BreakLoopBlock(Block):
    type_id, title, category, icon = "flow.break", "Break Loop", CAT, "end"
    description = "Leave the innermost Repeat / Repeat Until."
    cap = True

    def execute(self, ctx, p, node):
        raise BreakLoop


@register
class StopMacro(Block):
    type_id, title, category, icon = "flow.stop", "Stop Macro", CAT, "stop"
    description = "Stop the whole workflow immediately."
    cap = True
    params = (Param("message", "Message", "str", ""),)
    summary_fmt = "{message}"

    def execute(self, ctx, p, node):
        raise EndWorkflow(stopped=True, message=p["message"] or "Stopped by Stop Macro block")


@register
class SetVariable(Block):
    type_id, title, category, icon = "var.set", "Set Variable", CAT, "variable"
    description = "Store a value. Start the value with = to calculate, e.g. ={counter} + 1"
    params = (Param("name", "Variable", "var", "counter"),
              Param("value", "Value", "str", "0", tooltip="Text, a number, or =expression"))
    summary_fmt = "{name} = {value}"

    def execute(self, ctx, p, node):
        value = p["value"]
        ctx.set(p["name"], evaluate(value[1:]) if value.startswith("=") else coerce(value))


@register
class GetVariable(Block):
    type_id, title, category, icon = "var.get", "Get Variable", CAT, "variable"
    description = "Read a variable into another variable and show it in the console."
    params = (Param("name", "Variable", "var", "counter"),
              Param("output", "Copy to", "var", "", tooltip="Optional"))
    summary_fmt = "{name}"

    def execute(self, ctx, p, node):
        value = ctx.get(p["name"])
        if p["output"]:
            ctx.set(p["output"], value)
        ctx.log(f"{p['name']} = {fmt(value)}")
        return value


@register
class Comment(Block):
    type_id, title, category, icon = "flow.comment", "Comment", CAT, "comment"
    color = "#a89a56"
    description = "A note for people reading the workflow. Does nothing when run."
    params = (Param("text", "Comment", "str", "Note"),)
    summary_fmt = "{text}"

    def execute(self, ctx, p, node):
        pass


@register
class LogMessage(Block):
    type_id, title, category, icon = "util.log", "Log Message", "Utilities", "log"
    description = "Write a message to the console. Use {name} to include variables."
    params = (Param("message", "Message", "str", "Hello"),
              Param("level", "Level", "choice", "info", options=("info", "warning", "error")))
    summary_fmt = "{message}"

    def execute(self, ctx, p, node):
        ctx.log(p["message"], {"warning": logging.WARNING, "error": logging.ERROR}.get(p["level"], logging.INFO))
