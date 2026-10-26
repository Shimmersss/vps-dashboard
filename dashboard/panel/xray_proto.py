"""Wire subset of XTLS/Xray-core v26.3.27 app/stats/command/command.proto.

Uses a private descriptor pool; does not require protoc or modify the core.
"""
from google.protobuf import descriptor_pb2 as d, descriptor_pool, message_factory

f = d.FileDescriptorProto(name='panel/xray_stats.proto', package='xray.app.stats.command', syntax='proto3')


def message(name, fields):
    m = f.message_type.add(name=name)
    for name, number, kind, repeated in fields:
        field = m.field.add(name=name, number=number, label=3 if repeated else 1)
        if isinstance(kind, str):
            field.type = 11
            field.type_name = '.xray.app.stats.command.' + kind
        else:
            field.type = kind
    return m


message('QueryStatsRequest', [('pattern', 1, 9, False), ('reset', 2, 8, False)])
message('Stat', [('name', 1, 9, False), ('value', 2, 3, False)])
message('QueryStatsResponse', [('stat', 1, 'Stat', True)])
message('GetStatsRequest', [('name', 1, 9, False), ('reset', 2, 8, False)])
m = message('GetStatsOnlineIpListResponse', [('name', 1, 9, False)])
entry = m.nested_type.add(name='IpsEntry')
entry.options.map_entry = True
entry.field.add(name='key', number=1, type=9, label=1)
entry.field.add(name='value', number=2, type=3, label=1)
m.field.add(name='ips', number=2, type=11, type_name='.xray.app.stats.command.GetStatsOnlineIpListResponse.IpsEntry', label=3)

pool = descriptor_pool.DescriptorPool()
pool.Add(f)
for name in ['QueryStatsRequest', 'QueryStatsResponse', 'GetStatsRequest', 'GetStatsOnlineIpListResponse']:
    desc = pool.FindMessageTypeByName('xray.app.stats.command.' + name)
    globals()[name] = message_factory.GetMessageClass(desc) if hasattr(message_factory, 'GetMessageClass') else message_factory.MessageFactory(pool).GetPrototype(desc)

QUERY = '/xray.app.stats.command.StatsService/QueryStats'
ONLINE = '/xray.app.stats.command.StatsService/GetStatsOnlineIpList'
