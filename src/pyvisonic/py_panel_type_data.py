"""Panel Type data."""

from .py_enum import CFG

DEFAULT_PANEL_TYPE = -1

###################################################################################
##########################  Panel Type Information  ###############################
###################################################################################

#################################################################
######### Known Panel Types to work (or not) ####################
#    panel_type=0 : PowerMax , Model=21   Powermaster False  <<== THIS DOES NOT WORK (NO POWERLINK SUPPORT and only supports EPROM download i.e no sensor data) ==>>
#    panel_type=1 : PowerMax+ , Model=33   Powermaster False
#    panel_type=1 : PowerMax+ , Model=47   Powermaster False
#    panel_type=2 : PowerMax Pro , Model=22   Powermaster False
#    panel_type=4 : PowerMax Pro Part , Model=17   Powermaster False
#    panel_type=4 : PowerMax Pro Part , Model=62   Powermaster False
#    panel_type=4 : PowerMax Pro Part , Model=71   Powermaster False
#    panel_type=4 : PowerMax Pro Part , Model=86   Powermaster False
#    panel_type=5 : PowerMax Complete Part , Model=18   Powermaster False
#    panel_type=5 : PowerMax Complete Part , Model=79   Powermaster False
#    panel_type=7 : PowerMaster10 , Model=32   Powermaster True
#    panel_type=7 : PowerMaster10 , Model=68   Powermaster True   #  Under investigation. Problem with 0x3F Message data (EPROM) being less than requested
#    panel_type=7 : PowerMaster10 , Model=153  Powermaster True
#    panel_type=8 : PowerMaster30 , Model=6    Powermaster True
#    panel_type=8 : PowerMaster30 , Model=53   Powermaster True
#    panel_type=8 : PowerMaster30 , Model=63   Powermaster True   #  This is my test panel, all 0x3F  Message data is formatted correctly
#    panel_type=10: PowerMaster33 , Model=71   Powermaster True   #  Under investigation. Problem with 0x3F Message data (EPROM) being less than requested
#    panel_type=15: PowerMaster33 , Model=146  Powermaster True   #  Under investigation.
#    panel_type=16: PowerMaster360R, Model=??  Powermaster True   #  Under investigation.
#
#    panel_type=87: PowerMax Pro, Model=??  Powermaster False   #  Under investigation.  Norwegian PowerMax Pro - Github Issue #288
#################################################################


# Panel names keyed by protocol panel identifier.
#     0 : "PowerMax" is not a supported panel type
#     Assume 360R is Panel 16 for this release as it was released after the PM33, also I've an old log file from a user that indicates this
pmPanelType = {
    DEFAULT_PANEL_TYPE: "Default",
    0 : "PowerMax",
    1 : "PowerMax+",
    2 : "PowerMax Pro",
    3 : "PowerMax Complete",
    4 : "PowerMax Pro Part",
    5 : "PowerMax Complete Part",
    6 : "PowerMax Express",
    7 : "PowerMaster 10",
    8 : "PowerMaster 30",
    10 : "PowerMaster 33",
    13 : "PowerMaster 360",
    15 : "PowerMaster 33",
    16 : "PowerMaster 360R",
    87 : "PowerMax Pro",             # Issue #288; uses type 2 settings pending hardware validation.
}

# Config for each panel type.
#     Assume 360R is Panel 16 for this release as it was released after the PM33, also I've an old log file from a user that indicates this
#               So make column 16 the same as column 13
#     Don't know what 9, 11, 12 or 14 are so just copy other settings. I know that there are Commercial/Industry Panel versions so it might be them
#     This data defines each panel type's maximum capability
#     I know that panel types 4 and 5 support 3 partitions but I can't figure out how they are represented in A5 and A7 messages, so partitions only supported for PowerMaster and B0 messages

# Column identifiers for the configuration table below.
PANEL_TYPES = (DEFAULT_PANEL_TYPE, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 87)

type PanelConfigValues = tuple[bool, ...] | tuple[int, ...] | tuple[str, ...]
type PanelConfigRow = dict[int, bool | int | str]

_panel_config_rows: dict[CFG, PanelConfigValues] = {
    # Panel type:           -1,      0,      1,      2,      3,      4,      5,      6,      7,      8,      9,     10,     11,     12,     13,     14,     15,     16,     87
    CFG.SUPPORTED      : (  True,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True),
    CFG.KEEPALIVE      : (    15,      0,     25,     25,     25,     25,     25,     25,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     25),
    CFG.DLCODE_1       : ('5650',     '', '5650', '5650', '5650', '5650', '5650', '5650', 'AAAA', 'AAAA', 'AAAA', 'AAAA', 'AAAA', 'AAAA', 'AAAA', 'AAAA', 'AAAA', 'AAAA', '5650'),
    CFG.DLCODE_2       : ('AAAA',     '', 'AAAA', 'AAAA', 'AAAA', 'AAAA', 'AAAA', 'AAAA', '5650', '5650', '5650', '5650', '5650', '5650', '5650', '5650', '5650', '5650', 'AAAA'),
    CFG.DLCODE_3       : ('BBBB',     '', 'BBBB', 'BBBB', 'BBBB', 'BBBB', 'BBBB', 'BBBB', 'BBBB', 'BBBB', 'BBBB', 'BBBB', 'BBBB', 'BBBB', 'BBBB', 'BBBB', 'BBBB', 'BBBB', 'BBBB'),
    CFG.PARTITIONS     : (     1,      0,      1,      1,      1,      1,      1,      1,      3,      3,      3,      3,      3,      3,      3,      3,      3,      3,      1),
    CFG.EVENTS         : (   250,      0,    250,    250,    250,    250,    250,    250,    250,   1000,   1000,   1000,   1000,   1000,   1000,   1000,   1000,   1000,    250),
    CFG.KEYFOBS        : (     8,      0,      8,      8,      8,      8,      8,      8,      8,     32,     32,     32,     32,     32,     32,     32,     32,     32,      8),
    CFG.ONE_WKEYPADS   : (     8,      0,      8,      8,      8,      8,      8,      8,      0,      0,      0,      0,      0,      0,      0,      0,      0,      0,      8),
    CFG.TWO_WKEYPADS   : (     2,      0,      2,      2,      2,      2,      2,      2,      8,     32,     32,     32,     32,     32,     32,     32,     32,     32,      2),
    CFG.SIRENS         : (     2,      0,      2,      2,      2,      2,      2,      2,      4,      8,      8,      8,      8,      8,      8,      8,      8,      8,      2),
    CFG.USERCODES      : (     8,      0,      8,      8,      8,      8,      8,      8,      8,     48,     48,     48,     48,     48,     48,     48,     48,     48,      8),
    CFG.REPEATER       : (     0,      0,      0,      0,      0,      0,      0,      0,      4,      8,      4,      4,      4,      4,      4,      4,      4,      4,      0),
    CFG.PROXTAGS       : (     0,      0,      0,      8,      0,      8,      8,      0,      8,     32,     32,     32,     32,     32,     32,     32,     32,     32,      8),
    CFG.PANIC_BUTTON   : (     0,      0,      0,      8,      0,      8,      8,      0,      8,     32,     32,     32,     32,     32,     32,     32,     32,     32,      8),
    CFG.ZONECUSTOM     : (     5,      0,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5,      5),
    CFG.DEV_ZONE_TYPES : (    30,      0,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30,     30),
    CFG.WIRELESS       : (    28,      0,     28,     28,     28,     28,     28,     29,     29,     62,     62,     62,     62,     62,     64,     62,     62,     64,     28),
    CFG.WIRED          : (     2,      0,      2,      2,      2,      2,      2,      1,      1,      2,      2,      2,      2,      2,      0,      2,      2,      0,      2),
    CFG.SWITCH         : (    15,      0,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15,     15),
    CFG.PGM            : (     1,      0,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1,      1),
    CFG.AUTO_ENROL     : ( False,  False,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False,   True,   True,  False,  False),
    CFG.AUTO_SYNCTIME  : ( False,  False,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False),
    CFG.POWERMASTER    : ( False,  False,  False,  False,  False,  False,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False),
    CFG.EPROM_DOWNLOAD : (  True,  False,   True,   True,   True,   True,   True,   True,   True,  False,  False,  False,  False,  False,  False,  False,  False,  False,   True),
    CFG.AB_CRC_TYPE_ALT: ( False,  False,  False,  False,  False,  False,  False,  False,   True,   True,   True,   True,   True,   True,  False,   True,   True,  False,  False),
    CFG.INIT_SUPPORT   : ( False,  False,  False,  False,  False,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,   True,  False),
}

pmPanelConfig: dict[CFG, PanelConfigRow] = {
    setting: dict(zip(PANEL_TYPES, values, strict=True))
    for setting, values in _panel_config_rows.items()
}
