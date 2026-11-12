def classFactory(iface):
    from .sfm_plugin import SFMPlugin
    return SFMPlugin(iface)