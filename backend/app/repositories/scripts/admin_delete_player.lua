-- KEYS: player, leaderboard, click bucket, then matching game sessions.
-- ARGV[1]: player_id. Validate every fallible operation before writing.
if redis.call("EXISTS", KEYS[1]) == 0 then return 0 end
local kind = redis.call("TYPE", KEYS[2]).ok
if kind ~= "none" and kind ~= "zset" then
    return redis.error_reply("INVALID_LEADERBOARD_TYPE")
end
local sessions = {}
for i = 4, #KEYS do
    local owner = redis.call("GET", KEYS[i])
    if owner == ARGV[1] then sessions[#sessions + 1] = KEYS[i] end
end
redis.call("DEL", KEYS[1], KEYS[3])
redis.call("ZREM", KEYS[2], ARGV[1])
for _, key in ipairs(sessions) do redis.call("DEL", key) end
return 1
