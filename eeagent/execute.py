import os
import tempfile
import simplejson as json
from pidantic.supd.pidsupd import SupDPidanticFactory
from eeagent.eeagent_exceptions import EEAgentParameterException
from eeagent.util import _set_param_or_default


class PidWrapper(object):
    """
    This class wraps a pidantic pid.  The point of this class is to get an in-memory reference to the
    users launch request in the event that the pidantic object failed to run.  This minimzes lost messages
    in the event of sqldb errors, supervisord errors, or pyon errors.
    """
    REQUESTING = (100, "REQUESTING")
    RUNNING = (500, "RUNNING")
    TERMINATING = (600, "TERMINATING")
    INVALID = (900, "INVALID")
    REJECTED = (850, "REJECTED")
    FAILED = (800, "FAILED")
    EXITED = (1000, "EXITED")
    TERMINATED = (700, "TERMINATED")

    state_map = {}
    state_map["STATE_INITIAL"] = REQUESTING
    state_map["STATE_PENDING"] = REQUESTING
    state_map["STATE_STARTING"] = REQUESTING
    state_map["STATE_RUNNING"] = RUNNING
    state_map["STATE_STOPPING"] = TERMINATING
    state_map["STATE_STOPPING_RESTART"] = INVALID
    state_map["STATE_REQUEST_CANCELED"] = REJECTED
    state_map["STATE_TERMINATED"] = TERMINATED

    def __init__(self, exe, name, p=None):
        self._name = name
        self._pidantic = p
        self._exe = exe
        self._error_message = "Launch request lost on submission"

    def get_state(self):
        if not self._pidantic:
            return PidWrapper.FAILED

        state = self._pidantic.get_state()
        if state == "STATE_EXITED":
            if self._pidantic.get_result_code() != 0:
                new_state = PidWrapper.FAILED
            else:
                new_state = PidWrapper.EXITED
            # have to inspect for error
        else:
            new_state = PidWrapper.state_map[state]
        return new_state

    def get_name(self):
        return self._name

    def get_error_message(self):
        if self._pidantic:
            return self._pidantic.get_error_message()
        return self._error_message

    def set_pidantic(self, p):
        self._pidantic = p

    def set_error_message(self, msg):
        self._error_message = msg

    def terminate(self):
        if not self._pidantic:
            return
        self._pidantic.terminate()

    def clean_up(self):
        if not self._pidantic:
            return
        self._pidantic.cleanup()
        self._exe._remove_proc(self._name)
        
class PyonExe(object):

    def __init__(self):
        pass

class PyonRelExe(object):

    def __init__(self, eeagent_cfg):
        self.name = eeagent_cfg.name
        mandatory_args = ['pyon_directory', 'supd_directory']

        for a in mandatory_args:
            if a not in eeagent_cfg.launch_type:
                raise EEAgentParameterException("the %s of the pyon container must be set" % (a))

        self._pyon_dir = eeagent_cfg.launch_type.pyon_directory
        self._supdexe = SupDExe(eeagent_cfg)
        self._pyon_exe = os.path.join(self._pyon_dir, "bin/pycc")

        if "container_args" in eeagent_cfg.launch_type:
            pyon_args = eeagent_cfg.launch_type.container_args
        else:
            pyon_args = ""
        self.pyon_args = pyon_args.split()

    def run(self, name, parameters):
        # check parameters and massage into a supd call
        
        rel_file_str = "apps"

        if rel_file_str not in parameters:
            raise EEAgentParameterException("%s must be in the parameters for a pyon run" % (rel_file_str))
        rel_file_contents = parameters[rel_file_str]
        (osf, tmp_file) = tempfile.mkstemp(text=True)
        os.write(osf, json.dumps(rel_file_contents))
        os.close(osf)

        args = ["--rel", tmp_file] + self.pyon_args

        supd_params = {
            'exec': self._pyon_exe,
            'argv': args,
            'working_directory' : self._pyon_dir,
        }
        rc = self._supdexe.run(name, supd_params)
        #os.remove(tmp_file)
        return rc

    def get_known_pws(self):
        return self._supdexe.get_known_pws()

    def lookup_id(self, name):
        return self._supdexe.lookup_id(name)

    def get_all(self):
        return self._supdexe.get_all()

    def poll(self):
        return self._supdexe.poll()

    def terminate(self):
        self._supdexe.terminate()

class SupDExe(object):

    def __init__(self, eeagent_cfg):
        self._working_dir = eeagent_cfg.launch_type.supd_directory
        self._eename = eeagent_cfg.name
        supdexe = _set_param_or_default(eeagent_cfg.launch_type, 'supdexe', None)
        self._slots = int(eeagent_cfg.slots)
        self._factory = SupDPidanticFactory(directory=self._working_dir, name=self._eename, supdexe=supdexe)
        pidantic_instances = self._factory.reload_instances()
        self._known_pws = {}
        for name in pidantic_instances:
            pidantic = pidantic_instances[name]
            pw = PidWrapper(self, name)
            pw.set_pidantic(pidantic)
            self._known_pws[name] = pw

    def run(self, name, parameters):
        pw = PidWrapper(self, name)
        self._known_pws[name] = pw
        command = parameters['exec'] + " " + ' '.join(parameters['argv'])

        dir = self._working_dir
        if "working_directory" in parameters:
            dir = parameters["working_directory"]
        pid = self._factory.get_pidantic(command=command, process_name=name, directory=dir)
        pw.set_pidantic(pid)

        running_jobs = self._get_running()
        x = len(running_jobs)
        if x <= self._slots:
            pid.start()
        else:
            pid.cancel_request()
        return pw

    def get_known_pws(self):
        return self._known_pws
        
    def _remove_proc(self, proc_name):
        del self._known_pws[proc_name]

    def lookup_id(self, name):
        if name not in self._known_pws:
            return None
        return self._known_pws[name]

    def get_all(self):
        return self._known_pws
        
    def _get_running(self):
        running_states = [PidWrapper.RUNNING, PidWrapper.TERMINATING, PidWrapper.REQUESTING]
        a = self.get_all().values()
        running = [i.get_state() for i in a]

        running = [i for i in a if i.get_state() in running_states]
        return running

    def poll(self):
        return self._factory.poll()

    def terminate(self):
        self._factory.terminate()

def get_exe_factory(name, CFG):

    if name == "supd":
        factory = SupDExe(CFG.eeagent)
    elif name == "pyon":
        factory = PyonExe()
    elif name == "pyon_single":
        factory = PyonRelExe(CFG.eeagent)
    else:
        raise EEAgentParameterException("%s is an unknown launch type" % (name))

    return factory
