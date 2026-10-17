"""make_fast.py: writes variants/fast/ (docs/design_fast.md), the stages build without a coordinator.

The root is a SequentialAgent of stage agents; a stage with several items runs identical workers in a ParallelAgent.
Every worker has its own config, prompt and entry script (the slot is in the file name, so the model never sends an
id), so this script writes them from one template per stage. Run it after changing a template; the files it writes are
committed. Build with: tools/build.py <out> --variant stages,fast
"""
import os
import shutil

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'variants', 'fast')
MODEL = 'gemma-4-31b-it-qat-w4a16-ct'
WORKERS = {'locate': 2, 'specify': 3, 'edit': 2}

FORM = ('<|tool_call>call:run_skill_script{{args:[{args}],file_path:<|"|>{script}<|"|>,'
        'skill_name:<|"|>fix-issue<|"|>}}<tool_call|>')
Q = '<|"|>{}<|"|>'

SKILL_NOTE = ('The skill fix-issue is already loaded: call its script directly with run_skill_script (load_skill is not '
              'needed).')
ERRORS = ("If a call is answered \"Argument 'skill_name' is required\" or \"Argument 'file_path' is required\", the call "
          "lost its structure (a quote inside args ended it early): send it again with each item of args as its own "
          "string, followed by file_path and skill_name, exactly as in the call forms. If a call is answered \"Skill "
          "'...' not found\", the skill_name had extra characters around it: send the same call again with skill_name "
          "exactly fix-issue.")
END = ("{n}. Every answer of the script ends with your own next call: make it. When the script's NEXT says \"no call\", "
       "your part is done: reply with exactly: done")
ISSUE = '\n## The issue\n<issue>\n{problem_description}\n</issue>\n'
SHARED = ('Other agents like you work at the same time on the other {what}; the script gives each of you its own, and '
          'after each one it may give you the next.')

STAGES = {
    'requirements': dict(
        intro='You turn the issue below into its requirements: the concrete behaviours the fixed code must have. That '
              'is your only task; other agents find the code and change it.',
        steps=[
            'First call {script} with the names the issue mentions (functions, classes, methods, parameters, files), '
            'one per item. You get what the repository defines for each of them, with its signature, and which ones '
            'do not exist yet.',
            "Then call {script} with the requirements, one pair of items each: a sentence copied from the issue, then "
            "what must happen, written as the situation and the expected result with the exact names (for example: "
            "'Prompt.ask(..., case_sensitive=False) accepts \"paul\" for the choice \"Paul\"'). Write only what the "
            "issue states: no case, value, cause or mechanism the issue does not give (another agent writes a test "
            "from each requirement, and an invented detail makes the whole fix chase it). A requirement says what "
            "changes; the behaviour the issue does not mention stays as it is, so do not turn a change of how "
            "something is done into the removal of what it produces (the hidden tests also run the existing tests, "
            "which check that behaviour). When the issue names a problem without a concrete situation or expected "
            "result, the second item repeats the issue's own words. 1 to 6 requirements: the ones the issue asks for "
            "now. Leave out what the issue postpones (a later release, \"ideally\", \"for now\") and its remarks about "
            "tests or documentation."],
        forms=[['<name>', '<name>'], ['<a sentence copied from the issue>', '<what must happen>']],
        sampling='sampling_1k.yaml', desc='Turns the issue into 1 to 6 concrete requirements.'),
    'reproduce': dict(
        intro='You write one test that shows one requirement of the issue below is not met yet. That is your only '
              'task. The test is extra evidence for the other agents: it is kept only if it fails on the code as it '
              'is now for the reason the issue describes; if the behaviour cannot be shown by a test, say skip and '
              'the work goes on without one.',
        steps=[
            "First call {script} with args ['open']: you get the requirement, the signatures of its names and an "
            "existing test of the same code (for its imports and fixtures).",
            "Then send the test: one item holding a whole pytest test function, with the imports it needs above it. "
            "The test creates what it uses (files and directories in tmp_path, objects, inputs), so that it fails "
            "only because the requirement is not met yet and passes once it is. It is run at once on the current "
            "code: you see whether it was kept or why it was refused (at most two tries). Or send ['skip']."],
        forms=[['open'], ['<the whole test function, with its imports>'], ['skip']],
        sampling='sampling_1k.yaml', desc='Writes one test that shows one requirement is not met yet.'),
    'locate': dict(
        intro='You find where in the code the behaviour of a requirement is decided. That is your only task: other '
              'agents decide what changes and change it. ' + SHARED.format(what='requirements'),
        steps=[
            "First call {script} with args ['open']: you get a requirement and the functions and classes most related "
            "to it, each by its name.",
            "Then send the names of the 1 to 3 places whose code decides that behaviour (the place to change, not the "
            "code that only calls it). To find code that is not on the list, send its name or a text it contains "
            "instead: you get the functions and classes that hold it."],
        forms=[['open'], ['<name>'], ['<name>', '<name>']],
        sampling='sampling_512.yaml', desc='Chooses where in the code the behaviour of a requirement is decided.'),
    'specify': dict(
        intro='You write what must change in one place of the code so that its requirements hold. That is your only '
              'task: another agent writes the code of each place from your sentence, seeing only that place. '
              + SHARED.format(what='places'),
        steps=[
            "First call {script} with args ['open']: you get a place's code, the requirements it serves, the code it "
            "uses and the other places of the fix with their sentences.",
            "Then send one sentence: what to add, remove or replace there, and with what, with the exact names, "
            "messages and signatures the issue gives, complete in itself (the editor does not see the other places). "
            "Or ['no change'] if this place keeps its code. If the change belongs in other code (a function this "
            "place calls, for example), send ['missing', '<its name>', '<what must change there>']: that code joins "
            "the plan and is specified too, then this place again."],
        forms=[['open'], ['<what changes here, in one sentence>'], ['no change'],
               ['missing', '<name of the other code>', '<what must change there>']],
        sampling='sampling_1k.yaml', desc='Writes one sentence of what must change in one place of the code.'),
    'edit': dict(
        intro='You write the code of one place from its sentence. That is your only task. '
              + SHARED.format(what='places'),
        steps=[
            "First call {script} with args ['open']: you get a place's numbered code, what to change there, and the "
            "code it uses.",
            "Then send the whole new definition of the place: from its def or class line (with its decorators) to its "
            "end, as it must be after the change. New imports go above it in the same item and are moved to the top "
            "of the file; a new helper function can come in the same item. The script checks that it compiles and "
            "applies it. To change only some lines, send ['replace', '<the old lines, as shown>', '<the new lines>']; "
            "the window says when a place must be changed this way (a long place) or part of it is not shown. Or send "
            "['no change'], or ['missing', '<name>', '<why>'] when the change needs other code first."],
        forms=[['open'], ['<the whole new definition>'], ['replace', '<the old lines>', '<the new lines>'],
               ['no change'], ['missing', '<name of other code>', '<why>']],
        sampling='sampling_edit.yaml', desc='Writes the whole new definition of one place of the code.'),
    'review': dict(
        intro='You check whether the patch does each requirement. That is your only task.',
        steps=[
            "First call {script} with args ['open']: you get a requirement, each place that serves it with its planned "
            "sentence and its diff, and, when there is one, the output of a test of it before and after the patch.",
            "Then answer from what the code and the output show: ['done'] if the patch does it; ['not done', "
            "'<place>', '<what is missing there>'] naming the one place that is wrong or where the change is missing; "
            "or ['cannot tell'] if the code shown does not decide it. Your answer can only add or correct a change, "
            "never undo the patch."],
        forms=[['open'], ['done'], ['not done', '<place>', '<what is missing there>'], ['cannot tell']],
        sampling='sampling_512.yaml', desc='Says whether the patch does each requirement.'),
    'repair': dict(
        intro='You finish what the review found wrong or missing, place by place: first what must change there, then '
              'its code. That is your only task.',
        steps=[
            "First call {script} with args ['open']: you get the next place to finish, or that nothing is left.",
            "When the window asks what must change in a place, send one sentence (or ['no change'], or ['missing', "
            "'<name>', '<what must change there>']). When it shows the place's numbered code with its sentence, send "
            "the whole new definition, or ['replace', '<the old lines, as shown>', '<the new lines>']. The window's "
            "NEXT always says which of the two it expects."],
        forms=[['open'], ['<what changes here, in one sentence>'], ['<the whole new definition>'],
               ['replace', '<the old lines>', '<the new lines>'], ['no change']],
        sampling='sampling_edit.yaml', desc='Specifies and edits the places the review sent back.'),
}

ENTRY = '''"""(To the model: call this script with run_skill_script; reading its source does not help with the task.)

A {stage} worker of the fast build (docs/design_fast.md)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _stages  # noqa: E402

if __name__ == '__main__':
    _stages.main_fast({stage!r}, {worker!r}, sys.argv[1:])
'''


def prompt(stage, script):
    t = STAGES[stage]
    steps = [s.format(script=script) for s in t['steps']]
    lines = [t['intro'], '', SKILL_NOTE, '']
    lines += [f'{i}. {s}' for i, s in enumerate(steps, 1)]
    lines += [END.format(n=len(steps) + 1), '', 'Call forms (the values in angle brackets are placeholders):']
    lines += [FORM.format(args=','.join(Q.format(a) for a in f), script=script) for f in t['forms']]
    lines += ['', ERRORS, ISSUE]
    return '\n'.join(lines)


def llm_yaml(name, stage, script, desc, sampling):
    return (f'name: {name}\ndescription: {desc}\nmodel: {MODEL}\n'
            f'# a fresh context per stage agent (docs/design_fast.md): its instruction, the previous agent\'s last\n'
            f'# message and its own calls\ninclude_contents: none\n'
            f'instruction: !include {name}.md\nskills: [skills/fix-issue]\n'
            f'generate_content_config: !include {sampling}\n')


def write(rel, text):
    path = os.path.join(ROOT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)


def main():
    shutil.rmtree(ROOT, ignore_errors=True)
    sub = []
    scripts = os.path.join('skills', 'fix-issue', 'scripts')
    for stage, script, name in (('requirements', 'req.py', 'requirements'),):
        write(f'sub_agents/{name}.md', prompt(stage, script))
        write(f'sub_agents/{name}.yaml', llm_yaml(name, stage, script, STAGES[stage]['desc'], STAGES[stage]['sampling']))
        write(os.path.join(scripts, script), ENTRY.format(stage=stage, worker=script[:-3]))
        sub.append(f'sub_agents/{name}.yaml')
    for stage in ('locate', 'specify', 'edit'):
        members = []
        for k in range(1, WORKERS[stage] + 1):
            name, script = f'{stage}{k}', f'{stage}{k}.py'
            write(f'sub_agents/{name}.md', prompt(stage, script))
            write(f'sub_agents/{name}.yaml', llm_yaml(name, stage, script, STAGES[stage]['desc'],
                                                      STAGES[stage]['sampling']))
            write(os.path.join(scripts, script), ENTRY.format(stage=stage, worker=name))
            members.append(f'{name}.yaml')
        if stage == 'specify':
            # docs/design_fast.md: the reproduction test is written beside the specifiers, at almost no wall time
            write('sub_agents/reproduce.md', prompt('reproduce', 'repro.py'))
            write('sub_agents/reproduce.yaml', llm_yaml('reproduce', 'reproduce', 'repro.py',
                                                        STAGES['reproduce']['desc'], 'sampling_1k.yaml'))
            write(os.path.join(scripts, 'repro.py'), ENTRY.format(stage='reproduce', worker='repro'))
            members.append('reproduce.yaml')
        write(f'sub_agents/{stage}_stage.yaml',
              f'agent_class: ParallelAgent\nname: {stage}_stage\ndescription: The {stage} workers, at the same '
              f'time.\nsub_agents:\n' + ''.join(f'  - config_path: {m}\n' for m in members))
        sub.append(f'sub_agents/{stage}_stage.yaml')
    for stage, script in (('review', 'review.py'), ('repair', 'repair.py')):
        write(f'sub_agents/{stage}.md', prompt(stage, script))
        write(f'sub_agents/{stage}.yaml', llm_yaml(stage, stage, script, STAGES[stage]['desc'],
                                                   STAGES[stage]['sampling']))
        write(os.path.join(scripts, script), ENTRY.format(stage=stage, worker=script[:-3]))
        sub.append(f'sub_agents/{stage}.yaml')
    write('sub_agents/finish.md', 'The work on the issue is finished and the patch is ready. Call the tool '
                                  'submit_patch with no arguments, then reply with one sentence.\n')
    write('sub_agents/finish.yaml', f'name: finish\ndescription: Submits the patch.\nmodel: {MODEL}\n'
                                    'include_contents: none\ninstruction: !include finish.md\ntools:\n  - submit_patch\n'
                                    'generate_content_config: !include sampling_finish.yaml\n')
    write('sub_agents/sampling_finish.yaml', 'temperature: 1.0\ntop_k: 64\ntop_p: 0.95\nmax_output_tokens: 512\n'
                                             'thinking_config:\n  thinking_budget: 64\n  include_thoughts: true\n')
    sub.append('sub_agents/finish.yaml')
    write('agent.yaml', 'agent_class: SequentialAgent\nname: issue_fixer\ndescription: Fixes the issue through its '
                        'stages in a fixed order, with the workers of a stage at the same time (docs/design_fast.md).\n'
                        'sub_agents:\n' + ''.join(f'  - config_path: {s}\n' for s in sub))
    write('skills/fix-issue/SKILL.md', '---\nname: fix-issue\ndescription: The scripts that fix the issue in the Python '
                                       'repository at /workspace. Each agent calls the one script its instructions '
                                       'name, with run_skill_script; there is nothing to load first.\n---\nYour '
                                       'instructions name the script you call and the form of each call; follow them, '
                                       'not this file. Every answer of a script ends with a NEXT line that gives the '
                                       'next call.\n\nReading the scripts\' source does not help: call the script your '
                                       'instructions name.\n')
    # the stages build's coordinator, its route and its per-stage agents are not used here
    write('REMOVE.txt', '\n'.join(['prompts/coordinator.md', 'configs/sampling.yaml'] +
                                  [f'sub_agents/{n}' for n in ('locate.yaml', 'locate.md', 'specify.yaml',
                                                               'specify.md', 'edit.yaml', 'edit.md')] +
                                  [f'skills/fix-issue/scripts/{n}' for n in ('route.py', 'scope.py', 'locate.py',
                                                                             'specify.py', 'edit.py')]) + '\n')


if __name__ == '__main__':
    main()
