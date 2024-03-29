__author__ = 'yanivb'

from idaapi import Form
import DIE.Lib.DieConfig

class SettingsView(Form):

    def __init__(self):
        Form.__init__(self, r"""STARTITEM 0
BUTTON YES* Save Settings
BUTTON CANCEL Cancel
Form Setup View

Debbuging:
<##Maximal function calls:   {iMaxFuncCall}>
<##Maximal dereference depth:{iDerefDepth}>


Debug Values:
<Raw:{rRaw}>
<Parse:{rParse}>
<Array:{rArray}>
<Containers:{rContainer}>
<Dereference:{rDeref}>
<Arguments:{rArgs}>{cDebugValues}>

""", {
            'cDebugValues': Form.ChkGroupControl(("rRaw", "rParse", "rArray", "rContainer", "rDeref", "rArgs")),
            'iMaxFuncCall': Form.NumericInput(tp=Form.FT_DEC),
            'iDerefDepth': Form.NumericInput(tp=Form.FT_DEC),
        })

    def OnButtonNop(self, code=0):
        """Do nothing, we will handle events in the form callback"""
        pass




def Show(config_filename):
    global chooser
    die_config = DIE.Lib.DieConfig.get_config()

    settings = SettingsView()
    settings.Compile()

    settings.iMaxFuncCall.value = die_config.max_func_call
    settings.iDerefDepth.value = die_config.max_deref_depth

    settings.rDeref.checked = die_config.is_deref
    settings.rRaw.checked = die_config.is_raw
    settings.rParse.checked = die_config.is_parse
    settings.rArray.checked = die_config.is_array
    settings.rContainer.checked = die_config.is_container
    settings.rArgs.checked = die_config.get_func_args


    ok = settings.Execute()
    if ok ==1 :

        die_config.set_deref(settings.rDeref.checked)
        die_config.set_raw(settings.rRaw.checked)
        die_config.set_parse(settings.rParse.checked)
        die_config.set_array(settings.rArray.checked)
        die_config.set_container(settings.rContainer.checked)
        die_config.set_func_args(settings.rArgs.checked)

        die_config.set_max_deref_depth(settings.iDerefDepth.value)
        die_config.set_max_func_call(settings.iMaxFuncCall.value)


        print settings.iMaxFuncCall.value
        print settings.iDerefDepth.value

        die_config.save_configuration(config_filename)


