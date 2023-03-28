import os, time
from skills.sva_control import SkillControl
from bus.Message import Message
from bus.MsgBusClient import MsgBusClient
from framework.util.utils import LOG, Config
from threading import Event, Thread

from framework.message_types import (
        MSG_UTTERANCE, 
        MSG_SPEAK, 
        MSG_REGISTER_INTENT, 
        MSG_MEDIA,
        MSG_SYSTEM,
        MSG_RAW,
        MSG_SKILL
        )

class SimpleVoiceAssistant:
    def __init__(self, msg_handler=None, skill_id=None, skill_category=None, bus=None, timeout=5):
        """
        Most user skills at some point in their lifecycles 
        will want to speak() and/or play_media(). These
        require acquisition of a session and associated
        error logic which is shared among all skills by
        placing it here. 

        Skills wishing to play media call their play_media()
        method which handles initiating a session with the
        media service and playing the media. Establishing a
        session with the media player also causes a skill to
        go active and remain active until either the media 
        session is terminated or it ends normally. 

        A great deal of trouble went into supporting the 
        ability to interrupt oneself. For example, while 
        speaking an answer a user may wish to ask wiki yet
        another question. The intended behavior in this case
        is to stack the TTS output so when the new qustion
        has been answered the old question will automatically
        be resumed. While this behavior may not be desirable 
        to the developer it may be easily overridden in the 
        system skill (see the methods input_focus_determination() 
        and output_focus_determination() in the system skill 
        for more detailed information) making it far easier for
        the developer to disable then to implement.

        It is the same process with the tts service. A skill 
        enters the active state upon initiating a session with
        the tts service. In this case the tts service itself
        establishes a session with the media service. The skill
        calling the speak() method will enter the 'active' state 
        upon successful negotiation of the session with the tts
        service. It will remain in the active state until both 
        the session with the tts service and the underlying 
        session with the medai service have been terminated. This
        all happens automatically in this base class. 

        The base class maintains the last session response and 
        current session id for both the tts service and the media 
        service.
        """
        self.skill_control = SkillControl()
        self.skill_control.skill_id = skill_id
        self.skill_control.category = skill_category

        if bus is None:
            bus = MsgBusClient(self.skill_control.skill_id)
        self.bus = bus

        base_dir = str(os.getenv('SVA_BASE_DIR'))
        log_filename = base_dir + '/logs/skills.log'
        self.log = LOG(log_filename).log
        self.skill_base_dir = os.getcwd()
        self.handle_message = msg_handler
        self.log.debug("SimpleVoiceAssistant.__init__() initializing")

        self.focus_mode = 'speech'
        self.user_callback = None
        self.speak_callback = None
        self.bridge = Event()
        self.watchdog_thread = None

        self.i_am_active = False
        self.i_am_paused = False   # this skill has been paused by the user
        self.i_am_conversed = False
        self.done_speaking = True

        self.ignore_raw_ctr = 0
        cfg = Config()
        self.crappy_aec = cfg.get_cfg_val('Advanced.CrappyAEC')

        self._converse_callback = None
        self.activate_response = ''
        self.tts_session_response = ''
        self.tts_service_session_id = 0
        self.tts_service_session_ids = []
        self.media_session_response = ''
        self.media_player_session_id = 0
        self.stt_is_active = False
        self.waiting_for_input_focus = False
        self.intents = {}

        self.bus.on(MSG_UTTERANCE, self.handle_utterance)
        self.bus.on(MSG_SKILL, self.handle_skill_msg)
        self.bus.on(MSG_SYSTEM, self.handle_system_msg)
        self.bus.on(MSG_RAW, self.handle_raw_msg)

        # note we need a special handler for media but not Q&A because media handles system level
        # single verb utterances but Q&A does not.
        self.bus.on(MSG_MEDIA, self.handle_media_msg)

    # watchdog timer
    def start_watchdog(self, timeout, callback):
        timeout = int(timeout) * 1000
        while timeout > 0:
            time.sleep(0.001)
            timeout -= 1
 
            if self.bridge.is_set():
                self.log.debug("SimpleVoiceAssistant.start_watchdog() bridge is set - watchdog cancelled")
                self.bridge.clear()
                break

        self.log.debug("SimpleVoiceAssistant.start_watchdog() timeout = %s", (timeout))
        if timeout == 0:
            self.log.debug("SimpleVoiceAssistant.start_watchdog() watchdog timed out")
            callback()
        self.log.debug("SimpleVoiceAssistant.start_watchdog() watchdog ended")

    def watchdog_timeout(self):
        self.log.debug("SimpleVoiceAssistant.watchdog_timeout() starting")
        self.i_am_conversed = False
        info = {
                'error':'',
                'subtype':'release_input_focus',
                'skill_category':self.skill_control.category,
                'skill_id':'system_skill',
                'from_skill_id':self.skill_control.skill_id,
               }
        self.bus.send(MSG_SYSTEM, 'system_skill', info)
        if self.user_timeout_callback:
            self.user_timeout_callback()

    def cancel_watchdog(self):
        self.log.debug("SimpleVoiceAssistant.cancel_watchdog() stop watchdog thread")
        self.bridge.set()

    # special case confirm (yes/no) converse type path
    def confirm_converse_callback(self, response):
        self.log.debug("SimpleVoiceAssistant.confirm_converse_callback() confirm converse type path")
        self.cancel_watchdog()
        if response.lower() in ['yes', 'yea', 'yeah', 'yup', 'sure', 'ok', 'o.k.', 'correct', 'right']:
            return self.user_callback('yes')
        if response.lower() in ['no', 'nope', 'nah', 'not']:
            return self.user_callback('no')
        return self.user_callback('')

    def confirm_callback(self):
        self.log.debug("SimpleVoiceAssistant.confirm_callback() confirm converse type path")
        time.sleep(0.01)
        self.watchdog_thread = Thread(target=self.start_watchdog, args=(10,self.watchdog_timeout)).start()
        self.converse(self.confirm_converse_callback)

    def get_user_confirmation(self, callback, prompt=None, timeout_callback=None):
        self.log.debug("SimpleVoiceAssistant.get_user_confirmation() starting")

        # just a special case of get_user_input() but could be extended later to handle
        # different approaches if so desired.
        self.user_callback = callback
        self.user_timeout_callback = timeout_callback

        if prompt:
            self.speak(prompt, wait_callback=self.confirm_callback)
        else:
            self.confirm_callback()
    # special case confirm (yes/no) converse type path

    # standard converse type path
    def converse_callback(self, data):
        self.log.debug("SimpleVoiceAssistant.converse_callback() starting")
        self.cancel_watchdog()
        self.user_callback(data)

    def prompt_callback(self):
        self.log.debug("SimpleVoiceAssistant.prompt_callback() starting")
        self.converse(self.converse_callback)

    def get_user_input(self, callback, prompt=None, timeout_callback=None):
        self.log.debug("SimpleVoiceAssistant.get_user_input() starting")
        self.user_callback = callback
        self.user_timeout_callback = timeout_callback

        self.watchdog_thread = Thread(target=self.start_watchdog, args=(10,self.watchdog_timeout)).start()
        if prompt:
            self.speak(prompt, wait_callback=self.prompt_callback)
        else:
            self.prompt_callback()

    def handle_raw_msg(self, message):
        self.log.debug("SimpleVoiceAssistant.handle_raw_msg() starting")
        # special handling for the system skill 
        if self.skill_control.skill_id == 'system_skill':
            self.handle_message(message)
            return True

        # raw messages are ignored unless in converse mode
        if self.i_am_conversed:
            if self.crappy_aec == 'y':
                # ignore first stt on bad systems because it is probably what you just said
                self.log.info("SimpleVoiceAssistant.handle_raw_msg() skill_id = %s IGNORING = %s" % (self.skill_control.skill_id, message.data))
                if self.ignore_raw_ctr == 0:
                    self.ignore_raw_ctr += 1
                    return False
                self.ignore_raw_ctr = 0

            self.log.info("SimpleVoiceAssistant.handle_raw_msg %s ** Handle Raw Msg = %s" % (self.skill_control.skill_id,message))
            self.i_am_conversed = False
            info = {
                   'error':'',
                   'subtype':'release_input_focus',
                   'skill_category':self.skill_control.category,
                   'skill_id':'system_skill',
                   'from_skill_id':self.skill_control.skill_id,
                   }
            self.bus.send(MSG_SYSTEM, 'system_skill', info)
            self._converse_callback(message.data['utterance'])

    def send_message(self, target, message):
        self.log.debug("SimpleVoiceAssistant.send_message() starting")

        # send a standard skill message on the bus. message must be a dict
        from_skill_id = self.skill_control.skill_id
        message['from_skill_id'] = from_skill_id
        self.bus.send(MSG_SKILL, target, message)

    def play_media(self, file_uri, delete_on_complete='false', media_type=None):
        self.log.debug("SimpleVoiceAssistant.play_media() starting")

        # try to acquire a media player session and play a media file
        from_skill_id = self.skill_control.skill_id
        from_skill_category = self.skill_control.category

        if self.i_am_paused:
            if self.media_player_session_id:
                ## paused with active msid and play request
                ## need to reset paused session and reset pause
                # cancel existing session
                info = {
                       'error':'',
                       'subtype':'media_player_command',
                       'command':'cancel_session',
                       'session_id':self.media_player_session_id,
                       'skill_id':'media_player_service',
                       'from_skill_id':from_skill_id
                       }
                self.bus.send(MSG_MEDIA, 'media_player_service', info)
                self.i_am_paused = False
                self.media_player_session_id = 0
                time.sleep(0.1)

        if self.media_player_session_id != 0:
            ## already active msid and play request - need to reset active session and reset pause
            self.log.warning("SimpleVoiceAssistant.play_media() %s ** %s already has an active media session id=%s. play_media() reusing it" % (self.skill_control.skill_id,from_skill_id,self.media_player_session_id))
            info = {
                   'error':'',
                   'subtype':'media_player_command',
                   'command':'reset_session',
                   'file_uri':file_uri,
                   'session_id':self.media_player_session_id,
                   'skill_id':'media_player_service',
                   'from_skill_id':from_skill_id,
                   'media_type':media_type,
                   'delete_on_complete':delete_on_complete
                   }
            self.bus.send(MSG_MEDIA, 'media_player_service', info)
            return True

        # else we need to acquire a media session
        # BUG - these probably need to be stacked !!!
        self.file_uri = file_uri
        self.media_type = media_type
        self.delete_on_complete = delete_on_complete

        self.focus_mode = 'media'
        info = {
                'error':'',
                'subtype':'request_output_focus',
                'skill_id':'system_skill',
                'from_skill_id':from_skill_id,
                'skill_category':from_skill_category,
                }
        self.bus.send(MSG_SYSTEM, 'system_skill', info)
        return True

    def speak(self, text, wait_callback=None):
        self.log.debug("SimpleVoiceAssistant.speak() text = %s", (text))

        # send the text to the tts service
        if self.i_am_paused:
            if self.tts_service_session_id != 0:
                info = {
                        'error':'',
                        'subtype':'tts_service_command',
                        'command':'reset_session',
                        'session_id':self.tts_service_session_id,
                        'skill_id':'tts_service',
                        'from_skill_id':self.skill_control.skill_id,
                        }
                self.send_message('tts_service', info)
                time.sleep(0.1)
                info = {
                        'error':'',
                        'subtype':'tts_service_command',
                        'command':'resume_session',
                        'session_id':self.tts_service_session_id,
                        'skill_id':'tts_service',
                        'from_skill_id':self.skill_control.skill_id,
                        }
                self.send_message('tts_service', info)
                time.sleep(0.1)
                info = {'text': text, 'skill_id': self.skill_control.skill_id}
                self.bus.send(MSG_SPEAK, 'tts_service', info)
                self.i_am_paused = False
                return True

        self.focus_mode = 'speech'
        self.text = text
        info = {
                'error':'',
                'subtype':'request_output_focus',
                'skill_id':'system_skill',
                'from_skill_id':self.skill_control.skill_id,
                'skill_category':self.skill_control.category,
               }
        self.speak_callback = wait_callback
        self.bus.send(MSG_SYSTEM, 'system_skill', info)
        return True

    def speak_lang(self, mesg_file: str, mesg_info: dict, wait_callback=None):
        """
        Speak text from a file so as to support other languages 
        replace variables in the message file with their corresponding values in mesg_info
        """
        self.log.debug("SimpleVoiceAssistant.speak_lang() mesg_file = %s mesg_info = %s" % (mesg_file, mesg_info))
        if os.path.exists(mesg_file) == False:
          self.log.error("SimpleVoiceAssistant.speak_lang(): file %s not found" % (mesg_file))
          return False

        fh = open(mesg_file, "r")
        text = fh.read() 
        fh.close()
        for key in mesg_info:              # replace variables in the message with their corresponding values
          variable = "{"+key+"}"           # variables are surrounded by {braces}
          text = text.replace(variable, mesg_info[key])
        self.log.debug("SimpleVoiceAssistant.speak_lang() message text = %s" % (text))
        self.speak(text, wait_callback)    # speak the message 

    def register_intent(self, intent_type, verb, subject, callback):
        self.log.debug("SimpleVoiceAssistant.register_intent() intent_type = %s verb = %s subject = %s" % (intent_type, verb, subject))

        # bind a sentence type, subject and verb to a callback and send on message bus to intent service.
        subjects = subject
        verbs = verb
        if type(subject) is not list:
            subjects = [subject]

        if type(verb) is not list:
            verbs = [verb]

        for subject in subjects:
            for verb in verbs:
                key = intent_type + ':' + subject + ':' + verb

                if key in self.intents:
                    self.log.warning("** %s ** [%s]error - duplicate key %s" % (self.skill_control.skill_id, self.skill_control.skill_id, key))
                else:
                    self.intents[key] = callback

                    # for now assumes success but TODO needs time out and 
                    # parsing of response message because could be an intent clash 
                    info = {
                        'intent_type': intent_type,
                        'subject': subject,
                        'verb': verb,
                        'skill_id':self.skill_control.skill_id
                    }
                    self.bus.send(MSG_REGISTER_INTENT, 'intent_service', info)


    def handle_utterance(self, message):
        self.log.debug("SimpleVoiceAssistant.handle_utterance() message = %s", (message))

        # invokes callback based on verb:subject
        data = message.data
        data = data['utt']
        if self.skill_control.category == 'fallback':
            if data.get('skill_id','') == '':
                # special handling for fallback/Q&A skills
                if self.handle_fallback:
                    self.log.info("SimpleVoiceAssistant.handle_utterance()** %s ** handle_fallback() method!" % (self.skill_control.skill_id,))
                    self.handle_fallback(message)
                else:
                    self.log.error("SimpleVoiceAssistant.handle_utterance()** %s ** Exception - fallback skill has no handle_fallback() method!" % (self.skill_control.skill_id,))
        else:
            self.log.debug("SimpleVoiceAssistant.handle_utterance()** %s ** received utterance message! %s" % (self.skill_control.skill_id,data))
            if data.get('skill_id', '') == self.skill_control.skill_id:
                subject = ''
                verb = ''
                intent_type = 'C'
                if data['sentence_type'] == 'Q':
                    intent_type = 'Q'
                    subject = data['np']
                    verb = data['qword']
                else:
                    subject = data['subject']
                    verb = data['verb']

                subject = subject.replace(" the","")
                subject = subject.replace("the ","")

                key = data['intent_match']

                if key in self.intents:
                    #print("skill base class intent match: %s" % (key,))
                    (self.intents[key](message))


    def converse(self, callback):
        """
        a skill in the conversant state will get a 
        single raw utterance and then exit the 
        conversant state. the raw utterance is 
        returned to the caller
        """
        self.log.debug("SimpleVoiceAssistant.converse() starting")
        self.waiting_for_input_focus = True
        self._converse_callback = callback
        # inform the system skill of our intentions
        info = {
                'error':'',
                'subtype':'request_input_focus',
                'skill_id':'system_skill',
                'skill_category':self.skill_control.category,
                'from_skill_id':self.skill_control.skill_id,
                }
        self.bus.send(MSG_SYSTEM, 'system_skill', info)
        return True


    def send_release_output_focus(self):
        self.log.debug("SimpleVoiceAssistant.send_release_output_focus() starting")
        self.media_player_session_id = 0
        self.i_am_active = False
        info = {
                'error':'',
                'subtype':'release_output_focus',
                'skill_id':'system_skill',
                'from_skill_id':self.skill_control.skill_id,
                }
        self.bus.send(MSG_SYSTEM, 'system_skill', info)

    ## message bus handlers ##

    def handle_skill_msg(self, message):
        self.log.debug("SimpleVoiceAssistant.handle_skill_msg() skill message = %s" % (message))
        if message.data['skill_id'] == self.skill_control.skill_id:
            if message.data['subtype'] == 'media_player_command_response':
                self.media_session_response = message.data['response']
                if self.media_session_response == 'session_ended':
                    self.send_release_output_focus()

                elif self.media_session_response == 'session_paused':
                    info = {
                            'error':'',
                            'subtype':'pause_confirmed',
                            'skill_id':'system_skill',
                            'from_skill_id':self.skill_control.skill_id,
                            }
                    self.bus.send(MSG_SYSTEM, 'system_skill', info)

                elif self.media_session_response == 'session_confirm':
                    self.media_player_session_id = message.data['session_id']
                    # otherwise we are good to go
                    self.log.info("** %s ** Play media" % (self.skill_control.skill_id,))
                    self.i_am_active = True
                    info = {
                            'error':'',
                            'subtype':'media_player_command',
                            'command':'play_media',
                            'file_uri':self.file_uri,
                            'session_id':self.media_player_session_id,
                            'skill_id':'media_player_service',
                            'from_skill_id':self.skill_control.skill_id,
                            'media_type':self.media_type,
                            'delete_on_complete':self.delete_on_complete
                            }
                    self.bus.send(MSG_MEDIA, 'media_player_service', info)

            if message.data['subtype'] == 'tts_service_command_response':
                self.tts_session_response = message.data['response']
                if self.tts_session_response == 'session_ended':
                    if len(self.tts_service_session_ids) > 0:
                        self.tts_service_session_id = self.tts_service_session_ids.pop()
                        self.log.debug("SimpleVoiceAssistant.handle_skill_msg() restoring stacked session %s, stack=%s" % (self.tts_service_session_id, self.tts_service_session_ids))
                    else:
                        self.i_am_active = False

                    info = {
                            'error':'',
                            'subtype':'release_output_focus',
                            'skill_id':'system_skill',
                            'from_skill_id':self.skill_control.skill_id,
                            }
                    self.bus.send(MSG_SYSTEM, 'system_skill', info)
                    self.done_speaking = True
                    if self.speak_callback:
                        self.speak_callback()

                elif self.tts_session_response == 'paused_confirmed':
                    info = {
                            'error':'',
                            'subtype':'pause_confirmed',
                            'skill_id':'system_skill',
                            'from_skill_id':self.skill_control.skill_id,
                            }
                    self.bus.send(MSG_SYSTEM, 'system_skill', info)


                elif self.tts_session_response == 'session_confirm':
                    if self.tts_service_session_id != message.data['session_id'] and self.tts_service_session_id != 0:
                        self.tts_service_session_ids.append( self.tts_service_session_id )

                    self.tts_service_session_id = message.data['session_id']
                    info = {'text': self.text,'skill_id':self.skill_control.skill_id}
                    self.bus.send(MSG_SPEAK, 'tts_service', info)

            if self.handle_message is not None:
                self.handle_message(message)
        else:
            # else could be a stt_start/end notification 
            # which is useful for converse()
            if message.data['subtype'] == 'stt_start':
                self.stt_is_active = True

            if message.data['subtype'] == 'stt_end':
                self.stt_is_active = False


    def handle_media_msg(self, message):
        self.log.debug("SimpleVoiceAssistant.handle_media_msg() message = %s", (message))
        if message.data['skill_id'] == self.skill_control.skill_id:
            if self.handle_message is not None:
                self.handle_message(message)


    def pause_sessions(self):
        self.log.debug("SimpleVoiceAssistant.pause_sessions() starting")
        # pause any active media sessions
        if self.media_player_session_id != 0:
            info = {
                   'error':'',
                   'subtype':'media_player_command',
                   'command':'pause_session',
                   'session_id':self.media_player_session_id,
                   'skill_id':'media_player_service',
                   'from_skill_id':self.skill_control.skill_id,
                   }
            self.bus.send(MSG_MEDIA, 'media_player_service', info)
            self.i_am_paused = True

        # pause any active tts sessions
        if self.tts_service_session_id != 0:
            info = {
                   'error':'',
                   'subtype':'tts_service_command',
                   'command':'pause_session',
                   'session_id':self.tts_service_session_id,
                   'skill_id':'tts_service',
                   'from_skill_id':self.skill_control.skill_id,
                   }
            self.send_message('tts_service', info)
            self.i_am_paused = True

    def handle_system_msg(self, message):
        self.log.debug("SimpleVoiceAssistant.handle_system_msg() message = %s" % (message))
        if message.data['skill_id'] == self.skill_control.skill_id:
            if message.data['subtype'] == 'stop':
                if self.tts_service_session_id != 0:
                    # stop tts
                    info = {
                            'error':'',
                            'subtype':'tts_service_command',
                            'command':'stop_session',
                            'session_id':self.tts_service_session_id,
                            'skill_id':'tts_service',
                            'from_skill_id':self.skill_control.skill_id,
                            }
                    # don't do this here do it when you get the response
                    #self.tts_service_session_id = 0
                    self.send_message('tts_service', info)

                if self.media_player_session_id != 0:
                    # stop media player
                    info = {
                            'error':'',
                            'subtype':'media_player_command',
                            'command':'stop_session',
                            'session_id':self.media_player_session_id,
                            'skill_id':'media_player_service',
                            'from_skill_id':self.skill_control.skill_id,
                            }
                    self.media_player_session_id = 0
                    self.bus.send(MSG_MEDIA, 'media_player_service', info)
                    self.i_am_paused = False

                if self.stop:
                    # invoke user callback
                    try:
                        self.stop(message)
                    except Exception as e:
                        self.log.error("SimpleVoiceAssistant.handle_system_msg() Exception trying to invoke skill call back for skill = %s" % (self.skill_control.skill_id,))
                        self.log.error(e)
                else:
                    selg.log.error("SimpleVoiceAssistant.handle_system_msg() [%s]SVA_BASE stop received but no stop method!" %(self.skill_control.skill_id,))

            if message.data['subtype'] == 'request_input_focus_response':
                self.log.info("[%s] acquired input focus!" % (self.skill_control.skill_id,))
                # TODO error check and timeout check
                self.waiting_for_input_focus = False

                self.i_am_conversed = True
                self.ignore_raw_ctr = 0

            if message.data['subtype'] == 'request_output_focus_response':
                self.log.info("SimpleVoiceAssistant.handle_system_msg() [%s] acquired output focus!" % (self.skill_control.skill_id,))
                if message.data['status'] == 'confirm':
                    # if state speak else must be state media
                    if self.focus_mode == 'speech':
                        self.tts_session_response = ''
                        info = {
                               'error':'',
                               'subtype':'tts_service_command',
                               'command':'start_session',
                               'skill_id':'tts_service',
                               'from_skill_id':self.skill_control.skill_id
                               }
                        self.send_message('tts_service', info)
                    else:
                        info = {
                                'error':'',
                                 'subtype':'media_player_command',
                                 'command':'start_session',
                                 'skill_id':'media_player_service',
                                 'from_skill_id':self.skill_control.skill_id
                                }
                        self.bus.send(MSG_MEDIA, 'media_player_service', info)
                else:
                    self.log.warning("SimpleVoiceAssistant.handle_system_msg() [%s] Cannot acquire output focus!" % (self.skill_control.skill_id,))

            if message.data['subtype'] == 'pause':
                if self.i_am_paused:
                    self.log.debug("SimpleVoiceAssistant.handle_system_msg() IGNORE PAUSE BECAUSE ALREADY PAUSED!!!!!")
                    return
                return self.pause_sessions()

            if message.data['subtype'] == 'pause_internal':
                self.log.debug("SimpleVoiceAssistant.handle_system_msg() ALREADY PAUSED BUT CANT IGNORE INTERNAL PAUSE !!!!")
                return self.pause_sessions()

            if message.data['subtype'] == 'resume':
                # resume any active media sessions
                if self.media_player_session_id != 0:
                    info = {
                           'error':'',
                           'subtype':'media_player_command',
                           'command':'resume_session',
                           'session_id':self.media_player_session_id,
                           'skill_id':'media_player_service',
                           'from_skill_id':self.skill_control.skill_id,
                           }
                    self.bus.send(MSG_MEDIA, 'media_player_service', info)
                    self.i_am_paused = False

                # resume any active tts sessions
                if self.tts_service_session_id != 0:
                    info = {
                           'error':'',
                           'subtype':'tts_service_command',
                           'command':'resume_session',
                           'session_id':self.tts_service_session_id,
                           'skill_id':'tts_service',
                           'from_skill_id':self.skill_control.skill_id,
                           }
                    self.send_message('tts_service', info)
                    self.i_am_paused = False

            # honor any registered message handlers
            if self.handle_message is not None:
                self.handle_message(message)

