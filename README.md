# minimy
Minimy is a simple NLU-based voice assistant framework.
It is a fork of Ken-Mycroft's code at: https://github.com/ken-mycroft/minimy

## Overview
**From Ken Smith:**

"The goal of this project is to provide a run-time environment which facilitates the development of 
voice enabled applications. These voice enabled applications take the form of a 'skill' and are
simply python programs which may do normal python things as well as call speak() and listen() and
get called asynchronously when an utterance is matched to an intent the skill has previously registered."  

**From Mike Mac:**

I worked with Mycroft since 2019, but they went bankrupt in 2023, so had to move on. :((

I tried OVOS/Neon, but was not able to get it going after a couple weeks in early 2023.  I still haven't given up on that platform - no doubt it will only get better.

Then I found Minimy, and was able to get it running in a few hours. Apparently, it was a project that hoped to save Mycroft from the fire, but it wasn't well accepted. Thankfully, Ken put it up on github, I forked the code and here we are.  This is just a continuation of "give back to the community".

My environment is a Raspberry Pi running Ubuntu Desktop inside a 'boombox'. However, it should be portable to any hardware that can run Linux, and probably just about any distro (perhaps OVOS and RasPi OS are the two most likely), in any type of "enclosure" you fancy.

This document is based on 

## Installation
Installation should be run as a non-root user such as 'pi'. Run the install script:
```
$ ./install/linux_install.sh
```
This step can take up to ten minutes.
## Configuration
You can run a basic configuration with the command:
```
$ ./mmconfig.py
```
or, for more configuration options:
```
$ ./mmconfig.py sa
```

## Running
The system uses ./start.sh and ./stop.py to start and stop the system en masse. Each
skill and service run in their own process space and use the message bus or file system
to synchronize. Their output may be found in the directory named 'logs/'. 

The system relies on the environment variables ``SVA_BASE_DIR`` and ``GOOGLE_APPLICATION_CREDENTIALS``.
These are typically set in the start.sh script.

The SVA_BASE_DIR is set to the install directory of your system. The Google variable is set
based on where your Google Speech API key is located. 

Start a virtual environment
```
source venv_ngv/bin/activate<br/><br/>
```
Then start Minimy with:
```
$ ./start.sh
```
To stop Minimy:
```
$ ./stop.py
```
These must be run from the base directory.  The base directory is defined as where you installed this code to. 
For example:
```
/home/pi/minimy-mike99mac
```

If you don't have a Google Speech API key you 
can get one from here ...

https://console.cloud.google.com/freetrial/signup/tos

The start.sh file must then be modified to use this
key. The Google Python module actually requires this
enviornment variable but as mentioned it is typically 
set in the start.sh script. You could, if you like,
set it manually.

```
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/my/key/key_filename.json
```

## Configuration Explained

* ./mmconfig.py # basic
* ./mmconfig.py sa # super advanced options

<br/>
The system can use local or remote services for speech to text (STT), text to speech (TTS)
and intent matching. Intent matching is accomplished using natutal language processing (NLP) based on
the CMU link parser using a simpe enumerated approach referred to as shallow parsing.

As a result you will be asked during configuration if you would like to use remote or local STT, TTS
and NLP. Unless you have a good reason, for now you should always select local mode (remote=n) for NLP.

Remote TTS using polly requires an Amazon ID and key.  If you prefer to not use polly for remote TTS you may 
choose mimi2 from Mycroft which is a free remote TTS alternative. You could also select local only TTS in 
which case mimic3 should work fine.

By deault the system will fallback to local mode if a remote service fails. This will happen
automatically and result in a slower overall response. If the internet is going to be out
often you should probably just select local mode.  The differences are that remote STT is more accurate
and remote TTS sounds better. Both are slower but only slightly when given a reasonable internet
connection. Devices with decent connectivity should use remote for both.

You will also be asked for operating environment.  Currently the options are (p) for piOS, (l) for 
Ubuntu or (m) for the Mycroft MarkII running the Pantacor build.

This system relies on the concept of a wake word.  A wake word is one or more words which will cause 
the system to take notice of what you say next and then act upon this.

During configuration you will be asked to provide one or more words to act as wake words. You will
enter them separated by commas with no punctuation.  For example, 
```
hey Bubba, bubba
```
or
```
computer
```

Wake words work best when you choose multi-syllable words. Longer names like 'Esmerelda' or  words like
'computer' or words with distinct sounds like 'expression' (the 'x') or 'kamakazi' (two hard
'k's) will always work better than words like 'hey' or 'Joe'. You can use the test_recognition.sh 
script to see how well your recognition is working.  Just using the word 'computer' should work adequately.

You will also be asked to provide an input device index. If you do not know what this means enter the
value 0. If you would like to see your options you can run 'python framework/tests/list_input_devices.py'.
Remember, if you do not source your virtual environment first, things will not go well for you. 

So remember to always source the virtual environment before you run anything. The SVA_BASE_DIR and 
PYTHONPATH environments being set properly also helps.

You may also modify the default audio output device.  This value is used by the system aplay command 
and the system mpg123 command. To see your options run 
```
aplay -L
```
Which will produce a series of lines which look something like this ...
```
sysdefault:CARD=Headset
```
Remove the 'CARD=' and provide the value 
```
sysdefault:Headset
```
To the configuration program.

Local TTS refers to the local TTS engine.  Currently three are supported. Espeak, Coqui
and mimic3. Mimic3 is strongly recommended.

The Mark2 does not have Coqui as an option as it does not currently work on the Mark2. Espeak is
very fast but the sound quality is poor. Coqui sound quality is excellent but it takes forever
to produce a wav file (3-8 seconds). 

The crappy AEC value is used to determine if the system needs to work around poor quality AEC or
if it does not. Good quality AEC is typically provided by a standard set of headphones whereas
poor quality AEC is what you normally have if you were using a laptop's built in speaker and mic.

An easy way to test this setting in your environment would be to run the 'example 1' skill and see how 
well it recognizes you.
```
Hey Computer ---> run example one
```
Finally, you must provide a logging level. The characters 'e', 'w', 'i', 'd' correspond to the 
standard log levels. Specifically 'e' sets the log level to 'error' and 'd' sets it to 'debug', etc.

Once you confirm your changes you can see what was produced by typing 'cat install/mmconfig.yml'. You 
should not modify this file by hand even thought it may be enticing to do so.

