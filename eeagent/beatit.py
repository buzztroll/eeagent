import logging
import datetime
import simplejson as json
from eeagent.util import unmake_id

def beat_it(dashi, CFG, process_managers, log=logging):

    try:
        beat_msg = {}
        beat_msg['eeagent_id'] = ""
        beat_msg['timestamp'] = str(datetime.datetime.now())

        beat_processes = []
        # we can have many process managers per eeagent, walk them all to get all the processes
        for pm in process_managers:
            pm.poll()
            processes = pm.get_all()
            for pname in processes:
                p = processes[pname]
                (name, round) = unmake_id(p.get_name())
                beat_p = {'upid': name, 'round': round, 'state': p.get_state(), 'msg': p.get_error_message()}
                beat_processes.append(beat_p)
        beat_msg['processes'] = beat_processes

        log.log(logging.DEBUG, "Sending the heartbeat : %s" % (json.dumps(beat_msg)))
        dashi.fire(CFG.pd.name, "heartbeat", message=beat_msg)
    except Exception, ex:
        log.log(logging.ERROR, "Error Sending the heartbeat : %s" % (str(ex)))