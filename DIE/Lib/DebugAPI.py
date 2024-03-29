__author__ = 'yanivb'

#########################
#### General Imports ####
#########################
import sys
import os
import cProfile
import pstats
import StringIO
import time

from idaapi import *
from idautils import *
from idc import *

### DIE Imports###
import DIE.Lib.DieConfig
import DIE.Lib.DataParser
from DIE.Lib.DIE_Exceptions import FuncCallExceedMax, NewCodeSectionException
from DIE.Lib.CallStack import *
from DIE.Lib.DbgImports import *
from DIE.Lib.IDAConnector import get_cur_ea, is_call, is_ida_debugger_present, analyze_area
import DIE.Lib.DIEDb

##########################
####     Defines      ####
##########################
WAS_USER_BREAKPOINT = 0x1

class DebugHooker(DBG_Hooks):
    """
    IDA Debug hooking functionality
    """
    def __init__(self, is_dbg=False, is_dyn_bp=False):

        self.logger = logging.getLogger(__name__)
        self.config = DIE.Lib.DieConfig.get_config()
        data_parser = DIE.Lib.DataParser.getParser()

        plugin_path = self.config.parser_path

        data_parser.set_plugin_path(plugin_path)
        data_parser.loadPlugins()

        # Breakpoint Exceptions
        self.bp_handler = DIE.Lib.BpHandler.get_bp_handler()
        self.bp_handler.load_exceptions(DIE.Lib.DIEDb.get_db())

        ### Debugging ###
        DBG_Hooks.__init__(self)                        # IDA Debug Hooking API
        self.isHooked = False                           # Is debugger currently hooked

        self.runtime_imports = DbgImports()             # Runtime import addresses

        self.callStack = {}                             # Function call-stack dictionary
                                                        # (Key: ThreadId, Value: Thread specific Call-Stack)
        self.current_callstack = None                   # A pointer to the currently active call-stack

        self.prev_bp_ea = None                          # Address of previously hit breakpoint
        self.end_bp = None                              # If set framework will stop once this bp was reached

        self.start_time = None                          # Debugging start time
        self.end_time = None                            # Debugging end time

        ### Flags
        self.is_debug = is_dbg                         # Debug flag
        self.is_dyn_breakpoints = is_dyn_bp             # Should breakpoint be set dynamically or statically
        self.update_imports = True                      # IAT updating flag (when set runtime_imports will be updated)

        ### Debugging
        self.pr = None                                  # Profiling object (for debug only)

    def Hook(self):
        """
        Hook to IDA Debugger
        """

        if self.isHooked:   # Release any current hooks
            self.UnHook()

        try:
            if not is_ida_debugger_present():
                self.logger.error("DIE cannot be started with no debugger defined.")
                return

            self.logger.info("Hooking to debugger.")
            self.hook()
            self.isHooked = True

        except Exception as ex:
            self.logger.exception("Failed to hook debugger", ex)
            sys.exit(1)

    def UnHook(self):
        """
        Release hooks from IDA Debugger
        """
        try:
            self.logger.info("Removing previous debugger hooks.")
            self.unhook()
            self.isHooked = False

        except Exception as ex:
            self.logger.exception("Failed to hook debugger", ex)
            raise RuntimeError("Failed to unhook debugger")

    def update_iat(self):
        """
        Update the current IAT state and reset flag
        """
        self.runtime_imports.getImportTableData()
        self.update_imports = False

######################################################################
# Debugger Hooking Callback Routines


    def dbg_bpt(self, tid, ea):
        """
        'Hit Debug Breakpoint' Callback -
         this callback gets called once a breakpoint has been reached -
         this means we can either be in a CALL or a RET instruction.
        """
        try:

            # If final breakpoint has been reached. skip all further breakpoints.
            if self.end_bp is not None and ea == self.end_bp:
                self.logger.info("Final breakpoint reached at %s. context logging is stopped.", hex(ea))
                self.bp_handler.unsetBPs()
                request_continue_process()
                run_requests()
                return 0

            # If required, update IAT
            if self.update_imports:
                self.update_iat()

            # Set current call-stack
            if not tid in self.callStack:
                print "Creating new callstack for thread %d" % tid
                self.callStack[tid] = CallStack()

            self.current_callstack = self.callStack[tid]

            # Is this a CALL instruction?
            if is_call(ea):
                self.prev_bp_ea = ea  # Set prev ea
                if not self.is_debug:
                    request_step_into()  # Great, step into the called function
                    run_requests()  # Execute dbg_step_into callback.

            return 0

        except Exception as ex:
            self.logger.exception("Failed while handling breakpoint at %s:", ea, ex)
            return 1

    def dbg_step_into(self):
        """
        Step into gets called whenever we step into a CALL instruction.
        The callback checks if the function we have stepped into is a library function (in which case
        no BPs should be set inside it, so we need to skip to the next RET instruction), or we have
        stepped into a native function (in which case we just need to gather data and continue to next BP).
        """
        try:
            refresh_debugger_memory()
            ea = get_cur_ea()

            iatEA = None
            library_name = None

            # Is this a library function or a native one?
            if self.runtime_imports.is_func_imported(ea):
                iatEA, library_name = self.runtime_imports.find_func_iat_adrs(ea)

            # If stepped into an excepted function, remove calling bp and skip over.
            if self.bp_handler.is_exception_func(ea, iatEA):
                self.logger.debug("Removing breakpoint from %s", hex(self.prev_bp_ea))
                self.bp_handler.removeBP(self.prev_bp_ea)
                request_step_until_ret()
                run_requests()
                return 0

            # If this is a native function and dynamic break-pointing is set, add breakpoints to current function
            if iatEA is None and self.is_dyn_breakpoints:
                self.bp_handler.walk_function(ea)

            # Save CALL context
            func_call_num = self.current_callstack.push(ea, iatEA, library_name=library_name)

            # Check if total number of function calls exceeded the max configured value
            if func_call_num > self.config.max_func_call:
                raise FuncCallExceedMax()

            # Continue Debugging
            request_step_until_ret()
            run_requests()
            return 0

        except FuncCallExceedMax as ex:
            self.make_exception_last_func()

            # Continue Debugging
            request_step_until_ret()
            run_requests()
            return 0

        except NewCodeSectionException as ex:
            self.logger.info("Found new code segment")
            if ex.section_start is not None and ex.section_end is not None:
                self.logger.info("New code segment scope is %s - %s" % (hex(ex.section_start), hex(ex.section_end)))

            if not self.config.code_discovery:
                print "New code section has been reached."
                request_suspend_process()  # Suspend Execution
                run_requests()
                return 0

            self.logger.info("Analyzing new segment.")
            analyze_area(ex.section_start, ex.section_end)

            # Continue Debugging
            request_step_until_ret()
            run_requests()
            return 0

        except Exception as ex:
            self.logger.exception("Failed while stepping into breakpoint: %s", ex)
            exit(1)

    def dbg_step_until_ret(self):
        """
        Step until return gets called when entering a library function.
        the debugger will stop at the next instruction after the RET.
        Context info needs to be collected here and execution should be resumed.
        """
        try:
            # Save Return Context
            self.current_callstack.pop()

            if not self.is_debug:
                request_continue_process()
                run_requests()

        except Exception as ex:
            self.logger.exception("Failed while stepping until return: %s", ex)

    def dbg_thread_start(self, pid, tid, ea):
        """
        TODO: debugging, should be implemented fully.
        @return:
        """
        try:
            # If no call-stack exist for this thread, create one.
            if not tid in self.callStack:
                self.callStack[tid] = CallStack()

            if not self.is_debug:
                request_continue_process()
                run_requests()

        except Exception as ex:
            self.logger.exception("Failed while handling new thread: %s", ex)

    #def dbg_thread_exit(self, pid, tid, ea, exit_code):

    def dbg_process_exit(self, pid, tid, ea, exit_code):
        """
        TODO: debugging, should be implemented fully.
        @return:
        """
        self.end_time = time.time()

        self.bp_handler.unsetBPs()

        die_db = DIE.Lib.DIEDb.get_db()

        die_db.add_run_info(self.callStack,
                            self.start_time,
                            self.end_time,
                            idaapi.get_input_file_path(),
                            idautils.GetInputFileMD5())

        self.bp_handler.save_exceptions(die_db)

    def dbg_process_start(self, pid, tid, ea, name, base, size):
        """
        TODO: debugging, should be implemented fully.
        @return:
        """
        return True

    def dbg_continue_process(self):
        return True

###############################################
# Convenience Function

    def make_exception_last_func(self):
        """
        Adds the last called function to exceptions
        @return: True if succeeded, otherwise False
        """
        try:
            (except_ea, except_name) = self.current_callstack.get_top_func_data()

            self.logger.debug("Function %s was called more then %d times.",
                              except_name, self.config.max_func_call)

            self.logger.debug("Removing breakpoint from %s", hex(self.prev_bp_ea))
            self.bp_handler.removeBP(self.prev_bp_ea)

            # Add function to exceptions, and reload breakpoints
            self.logger.debug("Adding address %s to exception list", except_ea)
            self.bp_handler.add_bp_ea_exception(except_ea)
            self.logger.debug("Adding function name %s to exception list", except_name)
            self.bp_handler.add_bp_funcname_exception(except_name, reload_bps=True)

            return True

        except Exception as ex:
            self.logger.exception("Error while creating exception: %s", ex)
            return False

###############################################
#   Debugging

    def start_debug(self, start_func_ea=None, end_func_ea=None, auto_start=False):
        """
        Start Debugging
        @param start_func_ea: ea of function to start debugging from
        @param end_func_ea: ea of function to stop debugging end
        @param auto_start: Automatically start the debugger
        @rtype : object
        """
        self.Hook()

        if start_func_ea is not None:
            self.is_dyn_breakpoints = True

            # If end function address was not explicitly defined, set to end of current function
            if end_func_ea is None:
                self.end_bp = DIE.Lib.IDAConnector.get_function_end_adr(start_func_ea)
                self.bp_handler.addBP(self.end_bp, "FINAL_BP")

            # Walk current function
            self.bp_handler.walk_function(start_func_ea)

        else:
            self.bp_handler.setBPs()

        # Set start time
        if self.start_time is None:
            self.start_time = time.time()

        # start the process automatically
        if auto_start:
            request_start_process(None, None, None)
            run_requests()

################################################################################
# Profiling, for debug usage only.

    def profile_start(self):
        """
        Start profiling the application.
        @return:
        """

        # Start Profiling
        self.pr = cProfile.Profile()
        self.pr.enable()

    def profile_stop(self):
        """
        Stop profiling the application and display results.
        @return:
        """
        # If profiling is activated:
        if self.pr is None:
            return False

        self.pr.disable()
        s = StringIO.StringIO()
        sortby = 'tottime'
        ps = pstats.Stats(self.pr, stream=s).sort_stats(sortby)
        ps.print_stats()

        print s.getvalue()