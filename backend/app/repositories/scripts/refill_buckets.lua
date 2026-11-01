-- KEYS: 충전할 버킷 키 목록
-- ARGV[1]: 최대 토큰 수
-- ARGV[2]: 이번 충전량

local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])

if not capacity or capacity <= 0
    or not refill or refill <= 0 then
    return redis.error_reply("INVALID_REFILL_CONFIG")
end

-- 쓰기 전에 모든 대상 검사
for _, key in ipairs(KEYS) do
    local tokens = redis.call("HGET", key, "tokens")

    if tokens then
        local value = tonumber(tokens)

        if not value or value < 0
            or value ~= math.floor(value) then
            return redis.error_reply("INVALID_BUCKET_DATA")
        end
    end
end

local now = redis.call("TIME")
local now_ms = now[1] * 1000 + math.floor(now[2] / 1000)
local updated = 0

for _, key in ipairs(KEYS) do
    local tokens = redis.call("HGET", key, "tokens")

    -- 만료된 버킷은 다시 생성하지 않음
    if tokens then
        local new_tokens = math.min(
            capacity,
            tonumber(tokens) + refill
        )

        redis.call(
            "HSET", key,
            "tokens", new_tokens,
            "last_refill_ms", now_ms
        )

        updated = updated + 1
    end
end

return updated