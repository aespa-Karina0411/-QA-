class SpatialParserAdapter:
    """
    空间解析适配器（Adapter）
    作用：把旧的 spatial_utils 包装成“接口对象”
    """

    def parse(self, raw_objects, frame_shape):
        from perception.spatial_utils import parse_environment
        return parse_environment(raw_objects, frame_shape)