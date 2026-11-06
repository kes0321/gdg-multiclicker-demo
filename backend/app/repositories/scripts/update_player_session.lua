-- KEYS: player, leaderboard, click bucket, session
-- ARGV: player_id, nickname, action, now_ms, capacity, bucket TTL, session TTL
-- Check ownership and all key types before changing any state.
if redis.call("GET", KEYS[4]) ~= ARGV[1]
    or redis.call("EXISTS", KEYS[1]) == 0 then
    return 0
end
if ARGV[3] ~= "rename" and ARGV[3] ~= "restart" then
    return redis.error_reply("INVALID_SESSION_ACTION")
end
if redis.call("TYPE", KEYS[1]).ok ~= "hash" then
    return redis.error_reply("INVALID_PLAYER_TYPE")
end
if ARGV[3] == "restart" then
    local leaderboard_type = redis.call("TYPE", KEYS[2]).ok
    local bucket_type = redis.call("TYPE", KEYS[3]).ok
    if leaderboard_type ~= "none" and leaderboard_type ~= "zset" then
        return redis.error_reply("INVALID_LEADERBOARD_TYPE")
    end
    if bucket_type ~= "none" and bucket_type ~= "hash" then
        return redis.error_reply("INVALID_BUCKET_TYPE")
    end
end

redis.call("HSET", KEYS[1], "nickname", ARGV[2])
if ARGV[3] == "restart" then
    redis.call("HSET", KEYS[1],
        "score", 0, "level", 1, "total_clicks", 0, "created_at_ms", ARGV[4])
    redis.call("ZADD", KEYS[2], 0, ARGV[1])
    redis.call("HSET", KEYS[3], "tokens", ARGV[5], "last_refill_ms", ARGV[4])
    redis.call("EXPIRE", KEYS[3], ARGV[6])
end
redis.call("EXPIRE", KEYS[4], ARGV[7])
return 1
