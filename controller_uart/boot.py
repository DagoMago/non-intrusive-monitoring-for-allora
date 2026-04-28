# This file is executed on every boot (including wake-boot from deepsleep)
#import esp
#esp.osdebug(None)
#import webrepl
#webrepl.start()

#As it is exposed in the [circuits README](docs/circuits_README.md), the controlled Raspberrys require a low-level logic value in the controller's
#GPIO used as a control pin, for them to have access to their power supply. For that reason, as we want "powered on" to be their default state,
#it's necessary to initialize those control pins in this boot file. 

from machine import Pin
from time import sleep

control = Pin(38, Pin.OUT)

control.value(0)


