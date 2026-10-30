"""vllm_hang_check.py <gemma4_tool_parser.py>: runs vLLM's gemma4 tool-call argument parser (vLLM 0.19.1,
vllm/tool_parsers/gemma4_tool_parser.py, fetched from GitHub) on call texts and reports which never return.

The hang (docs/score_gap.md, "What hangs vLLM"): in _parse_gemma4_array, an array item written without the <|"|>
delimiter is read as a bare value up to the next ',' or ']'; when that stop is a ']' inside the item's own text
(x[0], a list, a JSON-quoted string), the next pass starts on that ']', reads an empty bare value without moving,
and loops forever, appending empty items, inside the API server, so every later request waits."""
import multiprocessing as mp
import sys

D = '<|"|>'
CASES = {
    'delimited, ] inside': f'skill_name:{D}fix-issue{D},file_path:{D}think.py{D},args:[{D}R1 needs x[0] here{D}]',
    'bare, no ]': f'skill_name:{D}fix-issue{D},file_path:{D}think.py{D},args:[R1 needs a fix here]',
    'bare, x[0]': f'skill_name:{D}fix-issue{D},file_path:{D}think.py{D},args:[R1 needs x[0] to change]',
    'bare after a string': f'skill_name:{D}fix-issue{D},file_path:{D}edit1.py{D},args:[{D}replace{D},lines[3]]',
    'nested array, ] in a string': f'skill_name:{D}fix-issue{D},file_path:{D}x.py{D},args:[[{D}a]b{D}]]',
    'JSON quotes, d[k]': 'skill_name:"fix-issue",file_path:"think.py",args:["R1 needs d[k] set"]',
}


def _run(src, text, q):
    ns = {'json': __import__('json')}
    exec('STRING_DELIM = \'<|"|>\'\n' + src, ns)
    q.put(repr(ns['_parse_gemma4_args'](text))[:120])


def main(path):
    lines = open(path).read().split('\n')
    start = next(i for i, l in enumerate(lines) if l.startswith('def _parse_gemma4_value'))
    end = next(i for i, l in enumerate(lines) if l.startswith('class Gemma4ToolParser'))
    src = '\n'.join(lines[start:end])
    for name, text in CASES.items():
        q = mp.Queue()
        p = mp.Process(target=_run, args=(src, text, q))
        p.start()
        p.join(3)
        if p.is_alive():
            p.terminate()
            print(f'{name:30} HANGS')
        else:
            print(f'{name:30} ok   {q.get()}')


if __name__ == '__main__':
    main(sys.argv[1])
