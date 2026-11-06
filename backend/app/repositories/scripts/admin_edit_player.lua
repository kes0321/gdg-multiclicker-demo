-- KEYS: player hash, leaderboard. ARGV: id, score, level, expected score, expected level
if redis.call("EXISTS", KEYS[1]) == 0 then return "PLAYER_NOT_FOUND" end
local kind = redis.call("TYPE", KEYS[2]).ok
if kind ~= "none" and kind ~= "zset" then
    return redis.error_reply("INVALID_LEADERBOARD_TYPE")
end
if redis.call("HGET", KEYS[1], "score") ~= ARGV[4]
    or redis.call("HGET", KEYS[1], "level") ~= ARGV[5] then
    return "STATE_CHANGED"
end
redis.call("HSET", KEYS[1], "score", ARGV[2], "level", ARGV[3])
redis.call("ZADD", KEYS[2], ARGV[2], ARGV[1])
return "OK"
