"""底座内部使用的枚举型字符串常量。

单独成文件，避免 device / communication / environment 相互反向依赖。
"""
from __future__ import annotations


class Layer:
    """设备所属组织层级（工程功能组织，不限定安装位置）。"""
    PROCESS = "process"
    BAY = "bay"
    STATION = "station"
    COMMUNICATION = "communication"


class LinkType:
    """连接类型。

    ``physical`` / ``electrical`` 由环境模型处理；``hardwire`` / ``wired`` /
    ``wireless`` 由通信网络投递消息。
    """
    PHYSICAL = "physical"
    ELECTRICAL = "electrical"
    HARDWIRE = "hardwire"
    WIRED = "wired"
    WIRELESS = "wireless"

    # 由网络投递的连接类型
    MESSAGE_LINK_TYPES = (HARDWIRE, WIRED, WIRELESS)
    # 由环境模型处理的连接类型
    ENVIRONMENT_LINK_TYPES = (PHYSICAL, ELECTRICAL)


class BusinessType:
    """内部消息业务类型（依据详细开发设计 4.4）。"""
    SAMPLING = "sampling"      # 采样/状态数据
    STATUS = "status"          # 设备状态
    COMMAND = "command"        # 命令/设定
    FEEDBACK = "feedback"      # 执行反馈
    PROTECTION = "protection"  # 保护事件
    SYNC = "sync"              # 对时